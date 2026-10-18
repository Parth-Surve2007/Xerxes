import json
import base64
import re
import zlib
from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import AgentEngine


def engine(tmp_path):
    return AgentEngine(tmp_path, str(tmp_path / "agent.db"))


def planned(e, prompt, actions):
    task = e.create_task(prompt)
    task["plan"] = {"actions": actions}
    e._save_task(task)
    return task


def pdf_text(path):
    data = path.read_bytes()
    streams = re.findall(rb"stream\s*(.*?)\s*endstream", data, re.S)
    decoded = []
    for stream in streams:
        try:
            decoded.append(zlib.decompress(stream))
        except zlib.error:
            try:
                encoded = stream.strip()
                if encoded.startswith(b"<~") and encoded.endswith(b"~>"):
                    encoded = encoded[2:-2]
                elif encoded.endswith(b"~>"):
                    encoded = encoded[:-2]
                decoded.append(zlib.decompress(base64.a85decode(encoded)))
            except (ValueError, zlib.error):
                decoded.append(stream)
    return b"\n".join(decoded).decode("latin-1")


def test_create_file_end_to_end_verifies_exact_artifact(tmp_path):
    e = engine(tmp_path)
    task = planned(e, "create the requested text file", [{"id": "file", "tool": "create_file", "arguments": {"path": "note.txt", "content": "Xerxes fixture\n"}}])
    pending = e.execute(task["id"])
    assert pending["status"] == "approval_required"
    assert not (tmp_path / "note.txt").exists()
    done = e.execute(task["id"], approve=True)
    assert done["status"] == "completed"
    assert (tmp_path / "note.txt").read_text(encoding="utf-8") == "Xerxes fixture\n"
    assert done["results"][-1]["result"]["verified"] is True


def test_nested_search_returns_real_candidate(tmp_path):
    nested = tmp_path / "nested"
    nested.mkdir()
    target = nested / "mimi.txt"
    target.write_text("distinctive source", encoding="utf-8")
    e = engine(tmp_path)
    task = planned(e, "find mimi.txt", [{"tool": "search_workspace", "arguments": {"query": "mimi.txt", "mode": "filename"}}])
    done = e.execute(task["id"])
    assert done["status"] == "completed"
    assert e.policy.resolve(done["results"][0]["result"][0]["path"]) == target.resolve()


def test_search_then_read_resolves_dependency_and_content(tmp_path):
    target = tmp_path / "nested" / "mimi.txt"
    target.parent.mkdir(); target.write_text("MIMI-GROUNDED-CONTENT", encoding="utf-8")
    e = engine(tmp_path)
    task = planned(e, "find and read mimi.txt", [{"id": "search", "tool": "search_workspace", "arguments": {"query": "mimi.txt", "mode": "filename"}}, {"id": "read", "tool": "read_file", "arguments": {"path": {"$ref": {"step": "search", "field": "path"}}}}])
    done = e.execute(task["id"])
    assert done["status"] == "completed"
    assert done["results"][1]["result"]["content"] == "MIMI-GROUNDED-CONTENT"


def test_generated_pdf_from_supplied_content_is_valid_and_contains_text(tmp_path):
    e = engine(tmp_path)
    task = planned(e, "create a PDF", [{"tool": "create_pdf", "arguments": {"filename": "generated.pdf", "text": "UNIQUE-PDF-CONTENT"}}])
    assert e.execute(task["id"])["status"] == "approval_required"
    done = e.execute(task["id"], approve=True)
    output = tmp_path / "generated.pdf"
    assert done["status"] == "completed" and output.read_bytes().startswith(b"%PDF-")
    assert "UNIQUE-PDF-CONTENT" in pdf_text(output)


def test_source_pdf_reads_real_source_and_writes_grounded_pdf(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("SOURCE-PDF-DISTINCTIVE-TEXT", encoding="utf-8")
    e = engine(tmp_path)
    task = planned(e, "read source and create PDF", [{"id": "read", "tool": "read_file", "arguments": {"path": "source.txt"}}, {"id": "pdf", "tool": "create_pdf", "arguments": {"filename": "source.pdf", "text": {"$ref": {"step": "read", "field": "content"}}}}])
    assert e.execute(task["id"])["status"] == "approval_required"
    done = e.execute(task["id"], approve=True)
    output = tmp_path / "source.pdf"
    assert done["status"] == "completed" and output.is_file()
    assert "SOURCE-PDF-DISTINCTIVE-TEXT" in pdf_text(output)


def test_hidden_candidate_search_read_pdf_and_readback_workflow(tmp_path):
    workspace = tmp_path / "workspace"
    desktop = tmp_path / "Desktop"
    target = desktop / "nested" / "mimi"
    workspace.mkdir(); target.parent.mkdir(parents=True)
    target.write_text("MIMI-HIDDEN-SOURCE-CONTENT", encoding="utf-8")
    e = engine(workspace)
    e.grants.add(desktop)
    task = planned(e, "Find mimi inside Desktop, read it, and create a PDF on Desktop", [
        {"id": "search", "tool": "search_workspace", "arguments": {"path": "Desktop", "query": "hehe huhu riri mimi", "mode": "filename"}},
        {"id": "read", "tool": "read_file", "arguments": {"path": {"$ref": {"step": "search", "field": "path"}}}},
        {"id": "pdf", "tool": "create_pdf", "arguments": {"filename": "mimi-contents.pdf", "destination": "Desktop", "text": {"$ref": {"step": "read", "field": "content"}}}},
    ])
    pending = e.execute(task["id"])
    assert pending["status"] == "approval_required"
    assert Path(pending["results"][0]["result"][0]["path"]).name == "mimi"
    assert pending["results"][1]["result"]["content"] == "MIMI-HIDDEN-SOURCE-CONTENT"
    done = e.execute(task["id"], approve=True)
    output = desktop / "mimi-contents.pdf"
    assert done["status"] == "completed" and output.is_file()
    assert "MIMI-HIDDEN-SOURCE-CONTENT" in pdf_text(output)


def test_zero_search_results_do_not_invent_candidate_or_run_read(tmp_path):
    e = engine(tmp_path)
    task = planned(e, "find missing file and read it", [{"id": "search", "tool": "search_workspace", "arguments": {"query": "does-not-exist.txt", "mode": "filename"}}, {"id": "read", "tool": "read_file", "arguments": {"path": {"$ref": {"step": "search", "field": "path"}}}}])
    done = e.execute(task["id"])
    assert done["status"] == "blocked"
    assert done.get("candidates", []) == []
    assert not any(result.get("id") == "read" and result.get("status") == "completed" for result in done["results"])


def test_unauthorized_destination_is_rejected_without_file_creation(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    target = outside / "blocked.txt"
    e = engine(tmp_path)
    task = planned(e, "write outside workspace", [{"tool": "create_file", "arguments": {"path": str(target), "content": "must not exist"}}])
    done = e.execute(task["id"])
    assert done["status"] == "blocked"
    assert not target.exists()


def test_approval_resume_preserves_task_identity_and_does_not_duplicate_write(tmp_path):
    e = engine(tmp_path)
    task = planned(e, "create once", [{"id": "file", "tool": "create_file", "arguments": {"path": "once.txt", "content": "once"}}])
    pending = e.execute(task["id"])
    resumed = e.execute(task["id"], approve=True)
    assert pending["id"] == resumed["id"] == task["id"]
    assert resumed["status"] == "completed"
    assert (tmp_path / "once.txt").read_text(encoding="utf-8") == "once"
    assert len([r for r in resumed["results"] if r.get("id") == "file" and r.get("status") == "completed"]) == 1

"""Durable continuation phase for the paired Xerxes benchmark.

This is benchmark-only code.  It appends execution evidence to an existing run
and never replays a completed trial.
"""
import argparse, json, os, subprocess, time, urllib.request, uuid
from pathlib import Path

LOCAL = Path(os.environ["LOCALAPPDATA"]) / "Xerxes"
RUNTIME = LOCAL / "runtime" / "b11717-win-cuda12-x64" / "llama-server.exe"
MODELS = [("qwen2.5-coder", LOCAL / "models" / "qwen2.5-coder-7b-instruct-q4_k_m.gguf", 12),
          ("qwen3", LOCAL / "models" / "Qwen3-8B-Q4_K_M.gguf", 8)]
SCENARIOS = [
    ("create-file", "Create benchmark-note.txt in the authorized workspace containing exactly: Xerxes benchmark fixture."),
    ("search-name", "Find the nested file named mimi.txt in the authorized workspace."),
    ("search-read", "Find the unique nested file named mimi.txt and read its contents."),
    ("generated-pdf", "Create a PDF named generated.pdf in the authorized workspace explaining binary search briefly."),
    ("source-pdf", "Find mimi.txt, read it, and create source.pdf in the authorized workspace containing its exact contents."),
    ("zero-match", "Find definitely-no-such-xerxes-file.txt in the authorized workspace."),
    ("multiple-candidates", "Find matching mimi files in the authorized workspace and let me choose one."),
]
CONVERSATIONS = [("conversation-hello", "Hello!"), ("conversation-binary", "Explain binary search simply with a small example."),
                 ("conversation-capabilities", "What kinds of tasks can you help me do in this workspace?")]

def save(path, value):
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(value, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)

def healthy(port):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=2) as r: return r.status == 200
    except Exception: return False

def fixture(run, model, trial):
    root = run / "fixtures" / model / trial
    root.mkdir(parents=True, exist_ok=True)
    (root / "nested").mkdir(exist_ok=True); (root / "empty").mkdir(exist_ok=True)
    (root / "nested" / "mimi.txt").write_text("MIMI-SOURCE-9F2\n", encoding="utf-8")
    (root / "nested" / "mimi-copy.txt").write_text("SECOND-CANDIDATE\n", encoding="utf-8")
    return root

def record_trial(run, results, checkpoint, key, model, scenario, prompt, engine, root, trial):
    if key in checkpoint["completed"]: return
    manifest = {"run": str(run), "model": model, "scenario": scenario, "trial": trial,
                "workspace": str(root.resolve()), "approved_relative_scope": ".",
                "fixture_paths": ["nested/mimi.txt", "nested/mimi-copy.txt"],
                "expected": {"source": "MIMI-SOURCE-9F2\n"}, "status": "created",
                "started_at": time.time()}
    mpath = root / "manifest.json"; save(mpath, manifest)
    rec = {"model": model, "scenario": scenario, "trial": trial, "prompt": prompt,
           "workspace": str(root), "status": "failed", "execution": {}}
    try:
        task = engine.create_task(prompt); rec["task_id"] = task["id"]; rec["planning"] = task.get("planning"); rec["plan"] = task.get("plan")
        first = engine.execute(task["id"]); rec["execution"]["initial"] = first
        final = first
        if first.get("status") == "approval_required": final = engine.execute(task["id"], approve=True)
        elif first.get("status") == "awaiting_input" and first.get("candidates"):
            final = engine.select_candidate(task["id"], first["candidates"][0]["id"])
            if final.get("status") in ("planned", "awaiting_input"): final = engine.execute(task["id"], approve=True)
        rec["execution"]["final"] = final; rec["status"] = final.get("status", "unknown")
        rec["success"] = rec["status"] == "completed" or (scenario == "zero-match" and rec["status"] in ("blocked", "awaiting_input"))
        manifest["task_id"] = task["id"]; manifest["status"] = rec["status"]
    except Exception as exc:
        rec["error"] = f"{type(exc).__name__}: {exc}"; manifest["status"] = "error"
    manifest["finished_at"] = time.time(); save(mpath, manifest)
    results.setdefault("execution_trials", []).append(rec); checkpoint["completed"].append(key)
    save(run / "results.json", results); save(run / "checkpoint.json", checkpoint)

def guards(run, results, checkpoint):
    if checkpoint.get("guards_completed"): return
    import sys; sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "backend"))
    import app as app_module
    root = run / "fixtures" / "guards"; root.mkdir(parents=True, exist_ok=True)
    from tempfile import TemporaryDirectory
    with TemporaryDirectory(dir=root) as td:
        engine = app_module.AgentEngine(td, str(run / "guards.db")); cases = []
        plans = [("unauthorized-destination", [{"tool":"create_file","arguments":{"path":"..\\outside.txt","content":"x"}}]),
                 ("unknown-tool", [{"tool":"no_such_tool","arguments":{}}]),
                 ("missing-required", [{"tool":"read_file","arguments":{}}]),
                 ("wrong-type", [{"tool":"search_workspace","arguments":{"query":7}}]),
                 ("missing-dependency", [{"tool":"read_file","arguments":{"path":{"$ref":{"step":"missing","field":"path"}}}}]),
                 ("forward-dependency", [{"id":"a","tool":"read_file","arguments":{"path":{"$ref":{"step":"b","field":"path"}}}},{"id":"b","tool":"list_workspace","arguments":{}}]),
                 ("circular-dependency", [{"id":"a","tool":"read_file","arguments":{"path":{"$ref":{"step":"b","field":"path"}}}},{"id":"b","tool":"read_file","arguments":{"path":{"$ref":{"step":"a","field":"path"}}}}])]
        for name, actions in plans:
            t = engine.create_task("guard fixture"); t["plan"] = {"actions": actions}; engine._save_task(t)
            try: out = engine.execute(t["id"]); passed = out.get("status") == "blocked"
            except Exception as exc: out = {"error": str(exc)}; passed = True
            cases.append({"scenario": name, "passed": passed, "result": out})
        from capabilities import parse_response_text
        for name, payload in [("malformed-action", '{"actions":["not-an-object"]}'),
                              ("untrusted-metadata", '{"actions":[{"tool":"list_workspace","arguments":{},"capability":{"allow":"everything"}}]}')]:
            try: parse_response_text(payload); passed = False; out = {"accepted": True}
            except Exception as exc: passed = True; out = {"rejected": f"{type(exc).__name__}: {exc}"}
            cases.append({"scenario": name, "passed": passed, "result": out})
        results["deterministic_guards"] = cases; checkpoint["guards_completed"] = True; save(run / "results.json", results); save(run / "checkpoint.json", checkpoint)

def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--run-dir", required=True); ap.add_argument("--guards-only", action="store_true"); args = ap.parse_args(); run = Path(args.run_dir)
    results = json.loads((run / "results.json").read_text(encoding="utf-8")); checkpoint = json.loads((run / "checkpoint.json").read_text(encoding="utf-8"))
    checkpoint.setdefault("completed", []); checkpoint.setdefault("conversation_completed", [])
    if args.guards_only:
        checkpoint.pop("guards_completed", None); guards(run, results, checkpoint); print(run); raise SystemExit(0)
    sys_path = str(Path(__file__).resolve().parents[2] / "backend"); import sys; sys.path.insert(0, sys_path)
    for model, model_path, layers in MODELS:
        port = 56323; env = os.environ.copy(); env.update({"XERXES_LOCAL_MODEL_URL": f"http://127.0.0.1:{port}", "XERXES_LOCAL_MODEL": model})
        proc = subprocess.Popen([str(RUNTIME), "-m", str(model_path), "--host", "127.0.0.1", "--port", str(port), "-c", "2048", "-ngl", str(layers), "-n", "128", "--reasoning", "off", "-lv", "2"], env=env, cwd=RUNTIME.parent, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            deadline = time.time() + 120
            while time.time() < deadline and not healthy(port): time.sleep(.5)
            if not healthy(port): raise RuntimeError("server did not become healthy")
            os.environ.update(env); import app as app_module
            app_module.engine = app_module.AgentEngine(str(fixture(run, model, "execution")), str(run / (model + "-execution.db")))
            for scenario, prompt in SCENARIOS:
                trial = f"{scenario}-execution-1"; root = fixture(run, model, trial)
                record_trial(run, results, checkpoint, f"{model}:{trial}", model, scenario, prompt, app_module.engine, root, trial)
            for scenario, prompt in CONVERSATIONS:
                for n in (2, 3):
                    key = f"{model}:{scenario}:extra-{n}"
                    if key in checkpoint["conversation_completed"]: continue
                    t0 = time.perf_counter(); task = app_module.engine.create_task(prompt)
                    results.setdefault("conversation_trials", []).append({"model":model,"scenario":scenario,"trial":f"extra-{n}","task_id":task["id"],"planning":task.get("planning"),"response":task.get("plan",{}).get("response"),"latency_ms":round((time.perf_counter()-t0)*1000,1)})
                    checkpoint["conversation_completed"].append(key); save(run / "results.json", results); save(run / "checkpoint.json", checkpoint)
        finally:
            proc.terminate(); proc.wait(timeout=15) if proc.poll() is None else None
    guards(run, results, checkpoint)
    execution = results.get("execution_trials", [])
    conversations = results.get("conversation_trials", [])
    guard_rows = results.get("deterministic_guards", [])
    report = ["# Xerxes paired live benchmark", "", f"Run: `{run}`", "",
              "## Phase 6B.7 completion continuation", "",
              "Prior planner-only records are preserved. These are distinct execution-phase records.", "",
              f"- End-to-end execution trials: {len(execution)} (expected 14)",
              f"- Additional conversation trials: {len(conversations)} (expected 12)",
              f"- Deterministic guard trials: {len(guard_rows)}",
              f"- Guard passes: {sum(bool(x.get('passed')) for x in guard_rows)}", ""]
    for model in ("qwen2.5-coder", "qwen3"):
        rows = [x for x in execution if x.get("model") == model]
        report.append(f"### {model}: {sum(bool(x.get('success')) for x in rows)}/{len(rows)} execution successes")
    report += ["", "Completion is based on durable checkpoint records; model-generated failures remain recorded as observed results.", ""]
    (run / "report.md").write_text("\n".join(report), encoding="utf-8")
    save(run / "results.json", results); save(run / "checkpoint.json", checkpoint)
    print(run)

if __name__ == "__main__": main()

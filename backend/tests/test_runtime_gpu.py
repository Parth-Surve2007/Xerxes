import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent import AgentEngine, LocalModel


def test_offload_log_parser_requires_explicit_positive_evidence():
    assert LocalModel.parse_offload_log("llama_load_tensors: offloaded 12 layers to GPU") == 12
    assert LocalModel.parse_offload_log("llama_load_tensors: offloaded 0 layers to GPU") == 0
    assert LocalModel.parse_offload_log("CUDA runtime initialized") is None


def test_observed_llama_layer_assignment_format_is_counted_without_defaults():
    lines = [
        "D load_tensors: layer 16 assigned to device CPU, is_swa = 0",
        "D load_tensors: layer 17 assigned to device CUDA0, is_swa = 0",
        "D load_tensors: layer 18 assigned to device CUDA0, is_swa = 0",
    ]
    ids = {LocalModel.parse_gpu_layer_log(line) for line in lines}
    ids.discard(None)
    assert ids == {17, 18}
    assert LocalModel.parse_gpu_layer_log("CUDA runtime initialized") is None


def test_layer_evidence_accepts_prefix_whitespace_and_rejects_malformed_lines():
    assert LocalModel.parse_gpu_layer_log("  I load_tensors: layer 28 assigned to device CUDA0, is_swa = 0") == 28
    assert LocalModel.parse_gpu_layer_log("layer assigned to device CUDA0") is None
    assert LocalModel.parse_gpu_layer_log("layer 12 assigned to device GPU") is None


def test_gpu_setting_persists_and_unverified_status_is_not_green(tmp_path, monkeypatch):
    settings = tmp_path / "runtime-settings.json"
    monkeypatch.setenv("XERXES_RUNTIME_SETTINGS", str(settings))
    monkeypatch.setenv("XERXES_LLAMA_SERVER_EXE", "missing-llama-server.exe")
    monkeypatch.setenv("XERXES_LOCAL_MODEL_PATH", "missing-model.gguf")
    e = AgentEngine(tmp_path, str(tmp_path / "agent.db"))
    result = e.model.set_gpu_mode(True)
    assert result["requested"] == "cuda"
    assert json.loads(settings.read_text())["gpu_mode"] == "cuda"
    status = e.model.status()
    assert status["gpu_acceleration_requested"] is True
    assert status["gpu_offload_verified"] is False
    assert status["gpu_layers_offloaded"] is None


def test_cpu_mode_reports_no_effective_gpu_offload(tmp_path, monkeypatch):
    monkeypatch.setenv("XERXES_RUNTIME_SETTINGS", str(tmp_path / "runtime-settings.json"))
    monkeypatch.setenv("XERXES_GPU_MODE", "cpu")
    monkeypatch.setenv("XERXES_LLAMA_SERVER_EXE", "missing-llama-server.exe")
    monkeypatch.setenv("XERXES_LOCAL_MODEL_PATH", "missing-model.gguf")
    status = AgentEngine(tmp_path, str(tmp_path / "agent.db")).model.status()
    assert status["backend_type"] == "cpu"
    assert status["gpu_layers"] == 0
    assert status["gpu_offload_verified"] is False


def test_mode_change_while_server_is_running_is_pending(monkeypatch, tmp_path):
    monkeypatch.setenv("XERXES_RUNTIME_SETTINGS", str(tmp_path / "runtime-settings.json"))
    model = AgentEngine(tmp_path, str(tmp_path / "agent.db")).model
    class Running:
        pid = 4242
        def poll(self): return None
        def terminate(self): pass
    model.process = Running()
    model.effective_gpu_mode = "cpu"
    result = model.set_gpu_mode(True)
    assert result == {"requested": "cuda", "effective": "cpu", "restart_pending": True}
    assert model.gpu_mode == "cuda"

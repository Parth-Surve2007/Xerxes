import ctypes
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from native_provider import NativeProvider


class FakeFn:
    def __init__(self, fn): self.fn = fn
    def __call__(self, *args): return self.fn(*args)


class FakeLib:
    def __init__(self):
        self.created = 0; self.destroyed = 0; self.generated = 0
        self.xerxes_model_create = FakeFn(self.create)
        self.xerxes_model_generate = FakeFn(self.generate)
        self.xerxes_model_destroy = FakeFn(self.destroy)
        self.xerxes_model_error = FakeFn(lambda: b"see per-call error buffer")
    def create(self, path, threads, handle, error, cap):
        self.created += 1; handle._obj.value = 123; return 0
    def generate(self, handle, prompt, max_tokens, output, out_cap, error, error_cap):
        self.generated += 1; value = b'{"actions": [{"tool": "list_workspace", "arguments": {}}]}'
        output.value = value; return len(value)
    def destroy(self, handle): self.destroyed += 1


def test_native_singleton_and_serialized_calls(monkeypatch, tmp_path):
    dll = tmp_path / 'native.dll'; model = tmp_path / 'model.gguf'; dll.write_bytes(b''); model.write_bytes(b'')
    fake = FakeLib()
    monkeypatch.setenv('XERXES_NATIVE_DLL', str(dll)); monkeypatch.setenv('XERXES_LOCAL_MODEL_PATH', str(model))
    provider = NativeProvider(); monkeypatch.setattr(ctypes, 'CDLL', lambda _: fake)
    assert 'list_workspace' in provider.plan('one')
    assert 'list_workspace' in provider.plan('two')
    assert fake.created == 1 and fake.generated == 2
    provider.stop(); assert fake.destroyed == 1


def test_native_missing_dll_is_distinct(monkeypatch, tmp_path):
    monkeypatch.setenv('XERXES_NATIVE_DLL', str(tmp_path / 'missing.dll'))
    monkeypatch.setenv('XERXES_LOCAL_MODEL_PATH', str(tmp_path / 'model.gguf'))
    status = NativeProvider().status()
    assert status['state'] == 'unavailable' and status['reason'] == 'native_dll_missing'

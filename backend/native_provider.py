"""Opt-in ctypes bridge for the Xerxes native llama.cpp adapter."""
import ctypes
import os
import threading
from pathlib import Path


class NativeProvider:
    def __init__(self):
        self.dll_path = Path(os.environ.get("XERXES_NATIVE_DLL", "")).expanduser()
        runtime_value = os.environ.get("XERXES_NATIVE_RUNTIME_DIR", "").strip()
        self.runtime_dir = Path(runtime_value).expanduser() if runtime_value else None
        self.model_path = Path(os.environ.get("XERXES_LOCAL_MODEL_PATH", "")).expanduser()
        self.threads = int(os.environ.get("XERXES_NATIVE_THREADS", "0"))
        self._dll_dir = None
        self._lib = None
        self._handle = None
        self._lock = threading.RLock()
        self._error = "not initialized"

    def _load(self):
        if self._lib is not None:
            return
        if not self.dll_path.is_file():
            raise RuntimeError(f"native DLL not found: {self.dll_path}")
        if os.name == "nt" and self.runtime_dir and self.runtime_dir.is_dir() and hasattr(os, "add_dll_directory"):
            self._dll_dir = os.add_dll_directory(str(self.runtime_dir))
        try:
            self._lib = ctypes.CDLL(str(self.dll_path))
        except OSError as exc:
            raise RuntimeError(f"native DLL could not load: {exc}") from exc
        try:
            self._lib.xerxes_model_create.argtypes = [ctypes.c_char_p, ctypes.c_uint32, ctypes.POINTER(ctypes.c_void_p), ctypes.c_char_p, ctypes.c_size_t]
            self._lib.xerxes_model_create.restype = ctypes.c_int
            self._lib.xerxes_model_generate.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32, ctypes.c_char_p, ctypes.c_size_t, ctypes.c_char_p, ctypes.c_size_t]
            self._lib.xerxes_model_generate.restype = ctypes.c_int
            self._lib.xerxes_model_destroy.argtypes = [ctypes.c_void_p]
            self._lib.xerxes_model_destroy.restype = None
            self._lib.xerxes_model_error.argtypes = []
            self._lib.xerxes_model_error.restype = ctypes.c_char_p
        except AttributeError as exc:
            raise RuntimeError(f"native DLL missing required symbol: {exc}") from exc

    def _ensure_handle(self):
        self._load()
        if self._handle:
            return
        if not self.model_path.is_file():
            raise RuntimeError(f"native model not found: {self.model_path}")
        handle = ctypes.c_void_p()
        error = ctypes.create_string_buffer(1024)
        code = self._lib.xerxes_model_create(str(self.model_path).encode(), self.threads, ctypes.byref(handle), error, len(error))
        if code != 0 or not handle.value:
            raise RuntimeError(f"native model initialization failed (code {code}): {error.value.decode(errors='replace')}")
        self._handle = handle

    def status(self):
        with self._lock:
            configured = bool(self.dll_path and self.model_path)
            if not configured:
                return {"state": "unconfigured", "provider": "llama.cpp-native", "model": str(self.model_path), "version": "local-adapter-v1", "diagnostic_mode": True}
            if not self.dll_path.is_file():
                return {"state": "unavailable", "provider": "llama.cpp-native", "model": str(self.model_path), "version": "local-adapter-v1", "diagnostic_mode": True, "reason": "native_dll_missing"}
            if not self.model_path.is_file():
                return {"state": "missing", "provider": "llama.cpp-native", "model": str(self.model_path), "version": "local-adapter-v1", "diagnostic_mode": True, "reason": "model_missing"}
            return {"state": "ready" if self._handle else "configured", "provider": "llama.cpp-native", "model": str(self.model_path), "version": "local-adapter-v1", "diagnostic_mode": False, "loaded": bool(self._handle)}

    def plan(self, prompt):
        with self._lock:
            self._ensure_handle()
            output = ctypes.create_string_buffer(8192)
            error = ctypes.create_string_buffer(1024)
            instruction = ("You are Xerxes planning safe workspace actions. Return ONLY one valid JSON object, with no explanation or markdown. "
                           "It must contain an actions array. Each action must contain tool and arguments. "
                           "Allowed tools are list_workspace, read_file, search_workspace, run_tests, apply_patch, create_pdf. "
                           "User request: " + prompt)
            code = self._lib.xerxes_model_generate(self._handle, instruction.encode(), 128, output, len(output), error, len(error))
            if code < 0:
                raise RuntimeError(f"native generation failed (code {code}): {error.value.decode(errors='replace')}")
            text = output.value.decode(errors="replace").strip()
            return text

    def stop(self):
        with self._lock:
            if self._handle and self._lib:
                self._lib.xerxes_model_destroy(self._handle)
            self._handle = None
            if self._dll_dir:
                self._dll_dir.close()
                self._dll_dir = None


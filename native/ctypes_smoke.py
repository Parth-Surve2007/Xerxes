import ctypes, os, sys, time
from pathlib import Path

dll=Path(sys.argv[1]); model=Path(os.environ['XERXES_MODEL_PATH'])
if os.name == 'nt':
  os.add_dll_directory(str(dll.parent))
  for dep in os.environ.get('XERXES_NATIVE_RUNTIME_DIR','').split(os.pathsep):
    if dep: os.add_dll_directory(dep)
lib=ctypes.CDLL(str(dll)); Handle=ctypes.c_void_p
lib.xerxes_model_create.argtypes=[ctypes.c_char_p,ctypes.c_uint32,ctypes.POINTER(Handle),ctypes.c_char_p,ctypes.c_size_t];lib.xerxes_model_create.restype=ctypes.c_int
lib.xerxes_model_generate.argtypes=[Handle,ctypes.c_char_p,ctypes.c_uint32,ctypes.c_char_p,ctypes.c_size_t,ctypes.c_char_p,ctypes.c_size_t];lib.xerxes_model_generate.restype=ctypes.c_int
lib.xerxes_model_destroy.argtypes=[Handle];lib.xerxes_model_destroy.restype=None
err=ctypes.create_string_buffer(512); h=Handle(); bad=lib.xerxes_model_create(b'Z:/missing-xerxes.gguf',2,ctypes.byref(h),err,len(err)); assert bad==2 and not h.value, (bad,err.value)
assert lib.xerxes_model_create(str(model).encode(),2,ctypes.byref(h),err,len(err))==0
try:
  for prompt in (b'Reply with exactly: native C API works.',b'Reply with exactly: second persistent generation works.'):
    out=ctypes.create_string_buffer(2048); start=time.perf_counter(); rc=lib.xerxes_model_generate(h,prompt,16,out,len(out),err,len(err)); elapsed=(time.perf_counter()-start)*1000; assert rc>0,(rc,err.value); print(f'elapsed_ms={elapsed:.0f} output={out.value.decode("utf-8", "replace")}')
finally: lib.xerxes_model_destroy(h)
print('ctypes smoke passed; invalid_path_exit=2')

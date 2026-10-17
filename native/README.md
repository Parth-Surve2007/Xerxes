# Xerxes native local inference adapter

`xerxes_llm.c` is C99 and calls the public llama.cpp C API. The llama.cpp engine itself remains C/C++.

Build against an upstream checkout:

```powershell
cmake -S native -B native/build -G Ninja -DLLAMA_CPP_DIR=C:/path/to/llama.cpp
cmake --build native/build --config Release
native/build/xerxes-llm.exe MODEL.gguf "Return a JSON object with an actions array" 64
```

The JSON Lines output includes `meta`, streamed `token`, `complete`, and error events. Model weights belong outside Git, preferably under `%LOCALAPPDATA%\Xerxes\models`. This adapter has not yet been connected to the Python planner or packaged into Electron; the current desktop application reports the real model state and remains diagnostic when no model is installed.

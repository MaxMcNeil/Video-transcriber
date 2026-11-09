#!/usr/bin/env bash
# Installe le moteur IA local (llama.cpp) sur le runner.
# 1) binaire officiel précompilé de la dernière version de llama.cpp
# 2) secours : compilation de llama-cpp-python (même API, plus lent à installer)
set -uo pipefail
mkdir -p llm/bin

essai_binaire() {
  local url
  local noms
  # 5 dernières versions : la toute dernière peut être en cours de publication
  noms="$(gh api "repos/ggml-org/llama.cpp/releases?per_page=5" -q '.[0].assets[].name' 2>&1)"
  # binaire CPU Linux x64 : contient ubuntu/linux + x64, sans variante GPU/ARM
  url="$(gh api "repos/ggml-org/llama.cpp/releases?per_page=5" \
        -q '.[].assets[] | select(.name | test("(ubuntu|linux)"; "i") and test("x64|x86_64|amd64"; "i")
                                 and (test("vulkan|cuda|rocm|hip|sycl|musa|openvino|kompute|arm|aarch64|s390|riscv|cann|opencl"; "i") | not)
                                 and test("\\.(zip|tar\\.gz|tgz)$"))
               | .browser_download_url' 2>/dev/null | head -n1)"
  if [ -z "$url" ]; then
    echo "Binaire llama.cpp CPU introuvable. Réponse / fichiers de la dernière version :"
    printf '%s\n' "$noms" | head -n 40
    return 1
  fi
  echo "Téléchargement : $url"
  curl -fsSL --retry 5 --retry-delay 5 -o llm/paquet "$url" || return 1
  case "$url" in
    *.zip) unzip -q -o llm/paquet -d llm/bin || return 1 ;;
    *)     tar xzf llm/paquet -C llm/bin || return 1 ;;
  esac
  rm -f llm/paquet
  local srv libdir
  srv="$(find "$PWD/llm/bin" -type f -name llama-server | head -n1)"
  [ -n "$srv" ] || return 1
  chmod +x "$srv"
  libdir="$(dirname "$(find "$PWD/llm/bin" -name 'libllama.so*' | head -n1)" 2>/dev/null)"
  export LD_LIBRARY_PATH="$(dirname "$srv"):${libdir:-}:${LD_LIBRARY_PATH:-}"
  "$srv" --version 2>&1 | tail -n2 || return 1
  {
    echo "LLAMA_SERVER=$srv"
    echo "LD_LIBRARY_PATH=$LD_LIBRARY_PATH"
  } >> "$GITHUB_ENV"
  echo "Moteur IA : binaire llama.cpp OK"
}

if essai_binaire; then
  exit 0
fi
echo "::warning::Binaire llama.cpp indisponible : compilation de secours (≈10 min)."
CMAKE_ARGS="-DGGML_NATIVE=ON" pip install -q "llama-cpp-python[server]" || {
  echo "::error::Impossible d'installer le moteur IA local."; exit 1; }
echo "Moteur IA : llama-cpp-python OK"

#!/usr/bin/env bash
# LightGBM's macOS wheel links @rpath/libomp.dylib and only searches Homebrew/MacPorts.
# Without Homebrew, reuse the libomp that scikit-learn bundles. Linux needs none of this.
set -euo pipefail
PY="${1:-.venv/bin/python}"
[[ "$(uname)" == "Darwin" ]] || { echo "not macOS, nothing to do"; exit 0; }
SITE=$("$PY" -c "import sysconfig; print(sysconfig.get_paths()['purelib'])")
LIB="$SITE/lightgbm/lib"
cp "$SITE/sklearn/.dylibs/libomp.dylib" "$LIB/libomp.dylib"
install_name_tool -id @rpath/libomp.dylib "$LIB/libomp.dylib"
install_name_tool -add_rpath @loader_path "$LIB/lib_lightgbm.dylib" 2>/dev/null || true
codesign --force -s - "$LIB/libomp.dylib" "$LIB/lib_lightgbm.dylib"
"$PY" -c "import lightgbm; print('lightgbm', lightgbm.__version__, 'loads')"

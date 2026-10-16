#!/usr/bin/env bash
# Native macOS arm64/x64 and Linux x64; exact upstream inputs shared with Windows.
set -euo pipefail
task_root="$(cd "$(dirname "$0")/../.." && pwd)"
python3 "$task_root/scripts/build/prepare_ffmpeg_sources.py"
task_work="$task_root/.artifacts/ffmpeg-build"
task_prefix="$task_work/prefix"
task_output="$task_root/.artifacts/portable-ffmpeg"
mkdir -p "$task_prefix" "$task_output/bin" "$task_output/licenses"
export PKG_CONFIG_LIBDIR="$task_prefix/lib/pkgconfig"
export PKG_CONFIG_PATH="$PKG_CONFIG_LIBDIR"
export CFLAGS="-O2 -fPIC"
export CXXFLAGS="$CFLAGS"
export LDFLAGS=""
task_ass=(-Dfontconfig=disabled -Ddirectwrite=disabled -Dcoretext=disabled -Drequire-system-font-provider=false)
if [[ "$(uname -s)" == Darwin ]]; then
  export MACOSX_DEPLOYMENT_TARGET=13.0
  export CFLAGS="$CFLAGS -mmacosx-version-min=13.0"
  export CXXFLAGS="$CFLAGS"
  export LDFLAGS="-mmacosx-version-min=13.0"
  task_jobs="$(sysctl -n hw.ncpu)"
  task_ass=(-Dfontconfig=disabled -Ddirectwrite=disabled -Dcoretext=enabled)
else
  task_jobs="$(nproc)"
fi
cd "$task_work/zlib"
./configure --static --prefix="$task_prefix"
make -j"$task_jobs"
make install
cd "$task_work/x264"
./configure --prefix="$task_prefix" --enable-static --enable-pic --disable-cli --disable-opencl
make -j"$task_jobs"
make install
function meson_library() {
  local task_name="$1"; shift
  meson setup "$task_work/$task_name/build-native" "$task_work/$task_name" \
    --prefix "$task_prefix" --libdir lib --buildtype release --default-library static --wrap-mode nodownload "$@"
  meson compile -C "$task_work/$task_name/build-native" -j "$task_jobs"
  meson install -C "$task_work/$task_name/build-native"
}
meson_library freetype -Dzlib=system -Dbzip2=disabled -Dpng=disabled -Dharfbuzz=disabled -Dbrotli=disabled -Dtests=disabled
meson_library harfbuzz -Dfreetype=enabled -Dglib=disabled -Dgobject=disabled -Dcairo=disabled -Dchafa=disabled \
  -Dicu=disabled -Dintrospection=disabled -Ddocs=disabled -Dtests=disabled -Dutilities=disabled
meson_library fribidi -Ddocs=false -Dbin=false -Dtests=false
meson_library libass -Dtest=disabled -Dcompare=disabled -Dprofile=disabled -Dfuzz=disabled -Dcheckasm=disabled \
  "${task_ass[@]}" -Dlibunibreak=disabled
cd "$task_work/ffmpeg"
./configure --prefix="$task_prefix" --pkg-config=pkg-config --pkg-config-flags=--static \
  --disable-autodetect --disable-shared --enable-static --disable-debug --disable-doc --disable-ffplay \
  --enable-gpl --enable-version3 --enable-libx264 --enable-zlib --enable-libass \
  --enable-libfreetype --enable-libharfbuzz --enable-libfribidi \
  --extra-cflags="-I$task_prefix/include $CFLAGS" --extra-ldflags="-L$task_prefix/lib $LDFLAGS"
cp ffbuild/config.log "$task_output/licenses/ffmpeg-config.log"
make -j"$task_jobs"
cp ffmpeg ffprobe "$task_output/bin/"
strip "$task_output/bin/ffmpeg" "$task_output/bin/ffprobe"
if [[ "$(uname -s)" == Darwin ]]; then
  codesign --force --sign - "$task_output/bin/ffmpeg"
  codesign --force --sign - "$task_output/bin/ffprobe"
fi
cp "$task_root/scripts/build/build_ffmpeg_unix.sh" "$task_work/sources/"
cp "$task_root/scripts/build/prepare_ffmpeg_sources.py" "$task_work/sources/"
cp "$task_root/desktop/ffmpeg-sources.json" "$task_output/licenses/"
cp COPYING.GPLv3 "$task_output/licenses/FFmpeg-GPLv3.txt"
cp LICENSE.md "$task_output/licenses/FFmpeg-LICENSE.md"
for task_lib in zlib x264 freetype harfbuzz fribidi libass; do
  mkdir -p "$task_output/licenses/$task_lib"
  find "$task_work/$task_lib" -maxdepth 2 -type f \( -iname '*license*' -o -iname 'copying*' -o -iname 'FTL.TXT' -o -iname 'GPLv2.TXT' \) \
    -exec cp '{}' "$task_output/licenses/$task_lib/" \;
done
{ uname -a; cc --version; meson --version; } > "$task_output/licenses/toolchain.txt"
python3 - "$task_work" "$task_output" <<'PY'
from pathlib import Path
import hashlib,json,shutil,sys
work,out=map(Path,sys.argv[1:])
shutil.copyfile(work/'ffmpeg/ffbuild/config.log',work/'sources/config.log')
shutil.copyfile(out/'licenses/toolchain.txt',work/'sources/toolchain.txt')
shutil.make_archive(str(out/'Corresponding-Source'), 'zip', work/'sources')
manifest={p.relative_to(out).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in out.rglob('*') if p.is_file()}
(out/'SHA256.json').write_text(json.dumps(manifest,indent=2)+'\n')
PY

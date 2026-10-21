#!/usr/bin/env bash
# Ubuntu 24.04 -> Windows x64. No opaque third-party FFmpeg binary is used.
set -euo pipefail
task_root="$(cd "$(dirname "$0")/../.." && pwd)"
python3 "$task_root/tools/build/prepare_ffmpeg_sources.py"
task_work="$task_root/.artifacts/ffmpeg-build"
task_prefix="$task_work/prefix"
task_output="$task_root/.artifacts/portable-ffmpeg"
mkdir -p "$task_prefix" "$task_output/bin" "$task_output/licenses"
export PKG_CONFIG_LIBDIR="$task_prefix/lib/pkgconfig"
export PKG_CONFIG_PATH="$PKG_CONFIG_LIBDIR"
export CFLAGS="-O2"
export CXXFLAGS="-O2"
export LDFLAGS="-static -static-libgcc -static-libstdc++"
cat > "$task_work/cross.ini" <<'EOF'
[binaries]
c = 'x86_64-w64-mingw32-gcc'
cpp = 'x86_64-w64-mingw32-g++'
ar = 'x86_64-w64-mingw32-ar'
strip = 'x86_64-w64-mingw32-strip'
windres = 'x86_64-w64-mingw32-windres'
pkg-config = 'pkg-config'
[host_machine]
system = 'windows'
cpu_family = 'x86_64'
cpu = 'x86_64'
endian = 'little'
[properties]
needs_exe_wrapper = true
EOF
task_jobs="$(nproc)"
cd "$task_work/zlib"
CC=x86_64-w64-mingw32-gcc AR=x86_64-w64-mingw32-ar RANLIB=x86_64-w64-mingw32-ranlib \
  ./configure --static --prefix="$task_prefix"
make -j"$task_jobs"
make install
cd "$task_work/x264"
./configure --host=x86_64-w64-mingw32 --cross-prefix=x86_64-w64-mingw32- --prefix="$task_prefix" \
  --enable-static --disable-cli --disable-opencl --extra-ldflags="$LDFLAGS"
make -j"$task_jobs"
make install
function meson_library() {
  local task_name="$1"; shift
  meson setup "$task_work/$task_name/build-win64" "$task_work/$task_name" \
    --cross-file "$task_work/cross.ini" --prefix "$task_prefix" --libdir lib \
    --buildtype release --default-library static --wrap-mode nodownload "$@"
  meson compile -C "$task_work/$task_name/build-win64" -j "$task_jobs"
  meson install -C "$task_work/$task_name/build-win64"
}
meson_library freetype -Dzlib=system -Dbzip2=disabled -Dpng=disabled -Dharfbuzz=disabled -Dbrotli=disabled -Dtests=disabled
meson_library harfbuzz -Dfreetype=enabled -Dglib=disabled -Dgobject=disabled -Dcairo=disabled -Dchafa=disabled \
  -Dicu=disabled -Dintrospection=disabled -Ddocs=disabled -Dtests=disabled -Dutilities=disabled
meson_library fribidi -Ddocs=false -Dbin=false -Dtests=false
meson_library libass -Dtest=disabled -Dcompare=disabled -Dprofile=disabled -Dfuzz=disabled -Dcheckasm=disabled \
  -Dfontconfig=disabled -Ddirectwrite=enabled -Dcoretext=disabled -Dlibunibreak=disabled
cd "$task_work/ffmpeg"
./configure --prefix="$task_prefix" --target-os=mingw32 --arch=x86_64 --cross-prefix=x86_64-w64-mingw32- \
  --pkg-config=pkg-config --pkg-config-flags=--static --disable-autodetect --disable-shared --enable-static \
  --disable-debug --disable-doc --disable-ffplay --enable-gpl --enable-version3 --enable-libx264 \
  --enable-zlib --enable-libass --enable-libfreetype --enable-libharfbuzz --enable-libfribidi \
  --extra-cflags="-I$task_prefix/include -O2" --extra-ldflags="-L$task_prefix/lib $LDFLAGS" --extra-libs=-lstdc++
cp ffbuild/config.log "$task_output/licenses/ffmpeg-config.log"
make -j"$task_jobs"
cp ffmpeg.exe ffprobe.exe "$task_output/bin/"
x86_64-w64-mingw32-strip "$task_output/bin/ffmpeg.exe" "$task_output/bin/ffprobe.exe"
# Keep all source archives and the exact recipe alongside the binary download.
cp "$task_root/tools/build/build_ffmpeg_windows.sh" "$task_work/sources/"
cp "$task_root/tools/build/prepare_ffmpeg_sources.py" "$task_work/sources/"
cp "$task_root/apps/desktop/ffmpeg-sources.json" "$task_output/licenses/"
cp COPYING.GPLv3 "$task_output/licenses/FFmpeg-GPLv3.txt"
cp LICENSE.md "$task_output/licenses/FFmpeg-LICENSE.md"
for task_lib in zlib x264 freetype harfbuzz fribidi libass; do
  mkdir -p "$task_output/licenses/$task_lib"
  find "$task_work/$task_lib" -maxdepth 2 -type f \( -iname '*license*' -o -iname 'copying*' -o -iname 'FTL.TXT' -o -iname 'GPLv2.TXT' \) \
    -exec cp '{}' "$task_output/licenses/$task_lib/" \;
done
cp /usr/share/doc/mingw-w64-common/copyright "$task_output/licenses/MinGW-copyright.txt"
cp /usr/share/doc/gcc-mingw-w64-base/copyright "$task_output/licenses/GCC-runtime-copyright.txt"
{ x86_64-w64-mingw32-gcc --version; meson --version; dpkg-query -W 'mingw-w64*' '*mingw*' 2>/dev/null; } > "$task_output/licenses/toolchain.txt"
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

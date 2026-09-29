# Licence texts for bundled libraries whose packages ship none

`tools/bundle_tesseract.py` copies each bundled library's licence from its package. A few MSYS2
packages (Windows build) include no licence file, so their upstream texts are kept here and used
as a fallback, looked up by package name.

| Library | Licence | Source |
|---|---|---|
| giflib | MIT | https://sourceforge.net/p/giflib/code/ci/master/tree/COPYING (identical to the 6.1.3 release) |
| lz4 (library) | BSD 2-Clause | https://github.com/lz4/lz4/blob/dev/lib/LICENSE |
| libidn2 | LGPL-3.0-or-later or GPL-2.0-or-later | https://gitlab.com/libidn/libidn2 (COPYING.LESSERv3, COPYINGv2, and COPYING, the GPLv3 that LGPLv3 builds on) |

If the build fails with "no licence text found for <package>", add that library's upstream licence
here under its package name.

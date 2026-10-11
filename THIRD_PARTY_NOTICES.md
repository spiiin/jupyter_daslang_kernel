# Third-party notices

The project's Python/C++ integration and frontend code use the license in
`LICENSE`. Third-party code retains its own copyright and license.

| Artifact | Component | License notice |
| --- | --- | --- |
| Kernel wheel | daslang headers / generated grammar | `_bin/daslang-LICENSE.txt`, `_bin/grammar-LICENSE.txt` |
| Kernel wheel | Tree-sitter and Unicode tables | `_bin/tree-sitter-LICENSE.txt`, `_bin/unicode-LICENSE.txt` |
| Frontend wheel | daslang grammar-derived token sets | `daslang-LICENSE.txt` in the prebuilt extension |
| Frontend wheel | Bundled JavaScript dependencies | Builder-generated `static/third-party-licenses.json` |
| Runtime ZIP | daslang and core runtime dependencies | `LICENSE`, `THIRD_PARTY_NOTICES.md`, `licenses/*` |

Runtime notice texts follow upstream's core SDK install recipe: uriparser,
dag_noise, vecmath, fmt, fast_float and Luau. The runtime bundle contains only
the two core DLLs and `.das` standard-library files; optional native modules,
example assets and Microsoft runtime DLLs are not redistributed here.

Python dependencies are installed separately by pip under their own licenses.
The frontend lockfile records dependency versions and license metadata; retain
the generated JavaScript notices when redistributing prebuilt assets.

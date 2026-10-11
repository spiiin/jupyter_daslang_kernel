cmake_minimum_required(VERSION 3.20)
project(daslang_notebook_parser LANGUAGES C)
set(DAS_SOURCE "C:/src/daScript" CACHE PATH "Current daslang source tree")
set(TS_ROOT "${DAS_SOURCE}/modules/dasTreeSitter/3rdparty/tree-sitter/lib")
add_library(notebook_parser SHARED
    "${TS_ROOT}/src/lib.c"
    "${DAS_SOURCE}/tree-sitter-daslang/src/parser.c"
    "${DAS_SOURCE}/tree-sitter-daslang/src/scanner.c")
target_include_directories(notebook_parser PRIVATE
    "${TS_ROOT}/include" "${TS_ROOT}/src" "${DAS_SOURCE}/tree-sitter-daslang/src")
set_target_properties(notebook_parser PROPERTIES WINDOWS_EXPORT_ALL_SYMBOLS ON C_STANDARD 11)

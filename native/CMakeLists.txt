cmake_minimum_required(VERSION 3.20)
project(notebook_runtime LANGUAGES CXX)
set(DAS_SOURCE "C:/src/daScript" CACHE PATH "Matching daslang runtime source tree")
set(DAS_LIB_DIR "${DAS_SOURCE}/lib/Release" CACHE PATH "Matching import-library directory")
file(SHA256 "${DAS_SOURCE}/bin/Release/libDaScriptDyn_runtime.dll" DAS_RUNTIME_HASH)
add_library(notebook_runtime SHARED context.cpp)
target_include_directories(notebook_runtime PRIVATE "${DAS_SOURCE}/include")
target_compile_features(notebook_runtime PRIVATE cxx_std_17)
target_compile_definitions(notebook_runtime PRIVATE DAS_ENABLE_DLL=1 DAS_ENABLE_EXCEPTIONS=1
    DAS_FREE_LIST=1 DAS_NO_ASSERTIONS DAS_FUSION=2 ITS_PLATFORM_WIN32=1 SIZE_OF_VOID_P=8
    NB_RUNTIME_HASH="${DAS_RUNTIME_HASH}")
target_compile_options(notebook_runtime PRIVATE /arch:AVX2 /utf-8 /wd4251 /wd4275)
target_link_libraries(notebook_runtime PRIVATE "${DAS_LIB_DIR}/libDaScriptDyn_runtime.lib")

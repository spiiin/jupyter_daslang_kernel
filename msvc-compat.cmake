# Local compatibility for MSVC 19.38's C4706 warning during LTCG.
# Keep daslang source files and its upstream warning policy unchanged.
if(MSVC AND MSVC_VERSION EQUAL 1938 AND CMAKE_SOURCE_DIR STREQUAL CMAKE_CURRENT_SOURCE_DIR)
    cmake_language(DEFER CALL target_compile_options libDaScriptDyn PRIVATE /wd4706)
endif()

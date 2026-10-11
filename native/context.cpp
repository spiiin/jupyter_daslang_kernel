#include "daScript/simulate/simulate.h"
#include "daScript/ast/ast.h"
#include <string>
#include "module_state.h"
#include "module_codec.h"
#include "notebook_files.h"

extern "C" __declspec(dllexport) const char *nb_runtime_hash() {
    return NB_RUNTIME_HASH;
}

extern "C" __declspec(dllexport) void *nb_context_make(int stackSize) {
    return new NotebookContext(uint32_t(stackSize));
}

extern "C" __declspec(dllexport) void nb_clear_trace(void *context) {
    auto *ctx = static_cast<NotebookContext *>(context);
    ctx->trace.clear();
    ctx->alwaysStackWalkOnException = true;
    ctx->showArgumentsOnException = false;
    ctx->showLocalVariablesOnException = false;
}

extern "C" __declspec(dllexport) const char *nb_get_trace(void *context) {
    return static_cast<NotebookContext *>(context)->trace.c_str();
}

extern "C" __declspec(dllexport) uint32_t nb_exception_line(void *context) {
    return static_cast<NotebookContext *>(context)->exceptionAt.line;
}

extern "C" __declspec(dllexport) const char *nb_exception_file(void *context) {
    auto *ctx = static_cast<NotebookContext *>(context);
    return ctx->exceptionAt.fileInfo ? ctx->exceptionAt.fileInfo->name.c_str() : "";
}

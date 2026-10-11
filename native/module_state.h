#pragma once
#include "daScript/ast/ast.h"
#include "daScript/simulate/simulate.h"
#include <filesystem>
#include <map>
#include <set>
#include <vector>

struct NotebookGlobal {
    int index;
    std::string module;
    std::string name;
};

struct NotebookContext : das::Context {
    explicit NotebookContext(uint32_t size) : das::Context(size) {}
    using das::Context::totalInitFunctions;
    using das::Context::initFunctions;
    using das::Context::totalVariables;
    using das::Context::globalVariables;
    using das::Context::functions;
    using das::Context::totalFunctions;
    using das::Context::globalInitStackSize;
    using das::Context::sharedOwner;
    std::string trace;
    std::string error;
    std::string metadata;
    std::vector<NotebookGlobal> imported;
    std::map<std::string, int> rootVariables;
    std::map<int, std::string> functionModules;
    std::set<std::string> modules;
    std::set<std::string> restoredModules;
    std::vector<das::SimNode *> deferredGlobals;
    std::vector<das::SimFunction *> deferredHooks;
    std::vector<uint8_t> bytes;

    void to_err(const das::LineInfo *at, const char *message) override {
        if (exception) trace += message ? message : "";
        else das::Context::to_err(at, message);
    }
};

// Installed before relocation; evaluated as the first global initializer, when
// the runtime has already built and sorted its complete init-function list.
struct NotebookInitGuard : das::SimNode {
    std::vector<das::SimNode *> *initializers;
    NotebookInitGuard(const das::LineInfo &at, std::vector<das::SimNode *> *nodes)
        : das::SimNode(at), initializers(nodes) {}
    das::SimNode *visit(das::SimVisitor &visitor) override {
        visitor.preVisit(this);
        visitor.op("NotebookDeferredInit");
        for (auto &node : *initializers) if (node) node = visitor.sub(node, "deferred_init");
        return visitor.visit(this);
    }
    vec4f eval(das::Context &context) override {
        auto &ctx = static_cast<NotebookContext &>(context);
        if (ctx.totalInitFunctions) {
            ctx.deferredHooks.assign(ctx.initFunctions, ctx.initFunctions + ctx.totalInitFunctions);
        }
        ctx.totalInitFunctions = 0;
        ctx.skipInitShutdownScript = true;
        return v_zero();
    }
};

struct NotebookCaptureAnnotation : das::FunctionAnnotation {
    NotebookCaptureAnnotation() : FunctionAnnotation("djnb_capture_init") {}
    bool apply(const das::FunctionPtr &, das::ModuleGroup &, das::AnnotationArgumentList &, das::string &) override { return true; }
    bool finalize(const das::FunctionPtr &, das::ModuleGroup &, das::AnnotationArgumentList &,
                  const das::AnnotationArgumentList &, das::string &) override { return true; }
    bool apply(das::ExprBlock *, das::ModuleGroup &, das::AnnotationArgumentList &, das::string &) override { return false; }
    bool finalize(das::ExprBlock *, das::ModuleGroup &, das::AnnotationArgumentList &,
                  const das::AnnotationArgumentList &, das::string &) override { return false; }
    bool simulate(das::Context *context, das::SimFunction *) override {
        auto &ctx = *static_cast<NotebookContext *>(context);
        for (int i = 0; i < ctx.totalVariables; ++i) {
            ctx.deferredGlobals.push_back(ctx.globalVariables[i].init);
            ctx.globalVariables[i].init = nullptr;
        }
        if (!ctx.totalVariables) return false;
        ctx.globalVariables[0].init = ctx.code->makeNode<NotebookInitGuard>(das::LineInfo(), &ctx.deferredGlobals);
        return true;
    }
};

inline std::string notebookModuleKey(das::Module *module) {
    std::error_code error;
    auto path = std::filesystem::weakly_canonical(std::filesystem::u8path(module->fileName.c_str()), error);
    return std::string(module->name.c_str()) + "|" +
        (error ? std::string(module->fileName.c_str()) : path.generic_u8string());
}

inline std::string notebookJson(const std::string &value) {
    std::string result = "\"";
    for (unsigned char ch : value) {
        if (ch == '\\' || ch == '"') { result += '\\'; result += char(ch); }
        else if (ch < 32) {
            const char *hex = "0123456789abcdef";
            result += "\\u00";
            result += hex[ch >> 4]; result += hex[ch & 15];
        } else result += char(ch);
    }
    return result + "\"";
}

extern "C" __declspec(dllexport) void nb_prepare_program(void *program, void *context) {
    auto &prog = *static_cast<das::Program *>(program);
    auto &ctx = *static_cast<NotebookContext *>(context);
    for (auto *mod : prog.library.getModules()) {
        const bool root = mod == prog.thisModule.get();
        const std::string key = root || (mod->builtIn && !mod->promoted) ? "" : notebookModuleKey(mod);
        if (!key.empty()) ctx.modules.insert(key);
        mod->globals.foreach([&](auto var) {
            const int index = prog.indexOf(var);
            if (index < 0 || !prog.isUsed(var)) return;
            if (root) ctx.rootVariables[var->name.c_str()] = index;
            else if (!key.empty()) ctx.imported.push_back({index, key, var->name.c_str()});
        });
        mod->functions.foreach([&](auto fn) {
            const int index = prog.indexOf(fn);
            if (index >= 0 && prog.isUsed(fn)) {
                // Functions are allocated during simulation; resolve pointers later.
                ctx.functionModules[index] = key;
            }
            if (root && fn->name == "djnb_cell") {
                auto annotation = new NotebookCaptureAnnotation();
                annotation->module = mod;
                auto declaration = new das::AnnotationDeclaration();
                declaration->annotation = annotation;
                fn->annotations.push_back(declaration);
            }
        });
    }
}

inline uint64_t notebookStorageHash(das::TypeInfo *info, int depth = 0) {
    if (!info || depth > 64) return 0;
    uint64_t hash = 14695981039346656037ull;
    auto add = [&](uint64_t value) { for (int i = 0; i < 8; ++i) {hash ^= uint8_t(value); hash *= 1099511628211ull; value >>= 8;} };
    auto name = [&](const char *text) { if (text) for (; *text; ++text) {hash ^= uint8_t(*text); hash *= 1099511628211ull;} add(0); };
    add(uint64_t(info->type)); add(info->hash); add(info->size);
    add(info->flags & (das::TypeInfo::flag_ref | das::TypeInfo::flag_isConst | das::TypeInfo::flag_isSmartPtr));
    add(info->dimSize);
    for (uint32_t i = 0; i < info->dimSize; ++i) add(info->dim[i]);
    if (info->type == das::Type::tArray || info->type == das::Type::tTable) {
        add(notebookStorageHash(info->firstType, depth + 1));
        if (info->type == das::Type::tTable) add(notebookStorageHash(info->secondType, depth + 1));
    } else if (info->type == das::Type::tStructure) {
        auto *st = info->getStructType();
        if (st) {
            add(st->size); add(st->count); name(st->name); name(st->module_name);
            for (uint32_t i = 0; i < st->count; ++i) {
                name(st->fields[i]->name); add(st->fields[i]->offset);
                add(notebookStorageHash(st->fields[i], depth + 1));
            }
        }
    } else if (info->type == das::Type::tEnumeration || info->type == das::Type::tEnumeration8 ||
               info->type == das::Type::tEnumeration16 || info->type == das::Type::tEnumeration64) {
        auto *en = info->getEnumType();
        if (en) {add(en->flags); add(en->count); for (uint32_t i = 0; i < en->count; ++i) {name(en->fields[i]->name); add(uint64_t(en->fields[i]->value));}}
    }
    return hash;
}

extern "C" __declspec(dllexport) const char *nb_module_metadata(void *context) {
    auto &ctx = *static_cast<NotebookContext *>(context);
    ctx.metadata = "{\"modules\":[";
    bool first = true;
    for (const auto &key : ctx.modules) {
        if (!first) ctx.metadata += ',';
        first = false; ctx.metadata += notebookJson(key);
    }
    ctx.metadata += "],\"variables\":[";
    first = true;
    for (const auto &var : ctx.imported) {
        if (!first) ctx.metadata += ',';
        first = false;
        ctx.metadata += "{\"index\":" + std::to_string(var.index) + ",\"module\":" +
            notebookJson(var.module) + ",\"name\":" + notebookJson(var.name) +
            ",\"hash\":" + std::to_string(notebookStorageHash(ctx.globalVariables[var.index].debugInfo)) + "}";
    }
    ctx.metadata += "]}";
    return ctx.metadata.c_str();
}

extern "C" __declspec(dllexport) int nb_root_index(void *context, const char *name) {
    auto &ctx = *static_cast<NotebookContext *>(context);
    auto found = ctx.rootVariables.find(name);
    return found == ctx.rootVariables.end() ? -1 : found->second;
}

extern "C" __declspec(dllexport) void nb_skip_global_init(void *context, int index) {
    auto &ctx = *static_cast<NotebookContext *>(context);
    if (index >= 0 && size_t(index) < ctx.deferredGlobals.size()) ctx.deferredGlobals[index] = nullptr;
}

extern "C" __declspec(dllexport) void nb_skip_module_init(void *context, const char *name) {
    static_cast<NotebookContext *>(context)->restoredModules.insert(name);
}

inline std::string notebookFunctionModule(NotebookContext &ctx, das::SimFunction *fn) {
    const int index = int(fn - ctx.functions);
    auto found = ctx.functionModules.find(index);
    return found == ctx.functionModules.end() ? "" : found->second;
}

extern "C" __declspec(dllexport) int nb_run_module_init(void *context) {
    auto &ctx = *static_cast<NotebookContext *>(context);
    ctx.clearException();
    ctx.restart();
    const bool ok = ctx.runWithCatch([&]() {
        char *ep, *sp;
        if (!ctx.stack.push(ctx.globalInitStackSize, ep, sp)) ctx.throw_error("notebook init stack overflow");
        vec4f args[2] = {das::cast<void *>::from(&ctx), das::cast<bool>::from(ctx.sharedOwner)};
        ctx.abiArg = args;
        ctx.abiCMRES = nullptr;
        das::FuncInfo frame{};
        frame.name = "notebook module initialization";
        frame.stackSize = ctx.globalInitStackSize;
        auto *prologue = reinterpret_cast<das::Prologue *>(ctx.stack.sp());
        prologue->info = &frame;
        prologue->arguments = nullptr;
        prologue->cmres = nullptr;
        for (auto *node : ctx.deferredGlobals) {
            if (node) {prologue->line = &node->debugInfo; node->eval(ctx);}
            if (ctx.stopFlags) break;
        }
        prologue->info = nullptr;
        ctx.abiArg = nullptr;
        ctx.stack.pop(ep, sp);
        if (!ctx.stopFlags) for (auto *fn : ctx.deferredHooks) {
            if (!ctx.restoredModules.count(notebookFunctionModule(ctx, fn))) ctx.callOrFastcall(fn, nullptr, 0);
            if (ctx.stopFlags) break;
        }
    });
    ctx.abiArg = nullptr;
    if (ok) ctx.restart();
    return ok ? 1 : 0;
}

extern "C" __declspec(dllexport) int nb_run_module_shutdown(void *context) {
    auto &ctx = *static_cast<NotebookContext *>(context);
    if (ctx.shutdown) return 1;
    ctx.clearException(); ctx.restart();
    ctx.shutdown = true;
    const bool ok = ctx.runWithCatch([&]() {
        std::vector<das::SimFunction *> late;
        for (int i = 0; i < ctx.totalFunctions; ++i) {
            auto *fn = &ctx.functions[i];
            if (!(fn->debugInfo->flags & das::FuncInfo::flag_shutdown)) continue;
            if (fn->debugInfo->flags & das::FuncInfo::flag_late_shutdown) late.push_back(fn);
            else ctx.callOrFastcall(fn, nullptr, 0);
            if (ctx.stopFlags) break;
        }
        if (!ctx.stopFlags) for (auto *fn : late) {
            ctx.callOrFastcall(fn, nullptr, 0);
            if (ctx.stopFlags) break;
        }
    });
    if (ok) ctx.restart();
    return ok ? 1 : 0;
}

extern "C" __declspec(dllexport) uint32_t nb_init_stack_size(void *program) {
    return static_cast<das::Program *>(program)->globalInitStackSize + 16384;
}


extern "C" __declspec(dllexport) void nb_compile_policy(void *policy, int fresh) {
    static_cast<das::CodeOfPolicies *>(policy)->ignore_shared_modules = fresh != 0;
}

extern "C" __declspec(dllexport) const char *nb_native_modules() {
    static std::string text;
    text = "[";
    bool first = true;
    das::Module::foreach([&](das::Module *module) {
        if (module->builtIn && !module->promoted) {
            if (!first) text += ',';
            first = false;
            text += notebookJson(module->name.c_str());
        }
        return true;
    });
    text += ']';
    return text.c_str();
}

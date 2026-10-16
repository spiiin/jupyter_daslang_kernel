#pragma once
#include <chrono>
#include <map>
#include <vector>

// Uses the same runtime function/allocation hooks as daslib/profiler.
struct NotebookProfiler : das::DebugAgent {
    using Clock = std::chrono::steady_clock;
    struct Row { uint64_t calls=0, ns=0, own=0, bytes=0, ownBytes=0, allocations=0; };
    struct Frame { das::SimFunction *fn; uint64_t start, children=0, bytes=0, childBytes=0, allocations=0; };
    struct Event { das::SimFunction *fn; uint64_t start, duration; };
    das::Context *target;
    bool previousAllocations;
    Clock::time_point epoch = Clock::now();
    std::map<das::SimFunction *, Row> rows;
    std::vector<Frame> stack;
    std::vector<Event> events;
    bool truncated=false;
    explicit NotebookProfiler(das::Context *ctx) : target(ctx), previousAllocations(ctx->instrumentAllocations) {}
    bool isCppOnlyAgent() const override { return true; }
    uint64_t now() const { return uint64_t(std::chrono::duration_cast<std::chrono::nanoseconds>(Clock::now()-epoch).count()); }
    void onInstrumentFunction(das::Context *ctx, das::SimFunction *fn, bool entering, uint64_t) override {
        if (ctx != target) return;
        auto ts=now();
        if (entering) { stack.push_back({fn,ts}); return; }
        if (stack.empty() || stack.back().fn != fn) return;
        auto frame=stack.back(); stack.pop_back();
        auto elapsed=ts-frame.start;
        auto &row=rows[fn]; ++row.calls; row.ns+=elapsed;
        row.own+=elapsed-frame.children; row.bytes+=frame.bytes;
        row.ownBytes+=frame.bytes-frame.childBytes; row.allocations+=frame.allocations;
        if (!stack.empty()) {
            auto &parent=stack.back(); parent.children+=elapsed;
            parent.bytes+=frame.bytes; parent.childBytes+=frame.bytes;
            parent.allocations+=frame.allocations;
        }
        if (events.size()<100000) events.push_back({fn,frame.start,elapsed});
        else truncated=true;
    }
    void allocation(das::Context *ctx, void *ptr, uint64_t bytes) {
        if (ctx==target && ptr && !stack.empty()) { stack.back().bytes+=bytes; ++stack.back().allocations; }
    }
    void onAllocate(das::Context *ctx, void *ptr, uint64_t bytes, const das::LineInfo &) override { allocation(ctx,ptr,bytes); }
    void onAllocateString(das::Context *ctx, void *ptr, uint64_t bytes, bool, const das::LineInfo &) override { allocation(ctx,ptr,bytes); }
    // Count a successful reallocation's requested new size, rather than its net growth.
    void onReallocate(das::Context *ctx, void *, uint64_t, void *ptr, uint64_t bytes, const das::LineInfo &) override { allocation(ctx,ptr,bytes); }
    std::string json() {
        std::string out="{\"functions\":["; bool first=true;
        for (auto &item:rows) {
            if (!first) out+=','; first=false;
            auto &r=item.second;
            out+="{\"name\":"+notebookJson(item.first->mangledName)+",\"calls\":"+std::to_string(r.calls)
                +",\"inclusive_ns\":"+std::to_string(r.ns)+",\"self_ns\":"+std::to_string(r.own)
                +",\"allocated_bytes\":"+std::to_string(r.bytes)+",\"self_allocated_bytes\":"+std::to_string(r.ownBytes)
                +",\"allocations\":"+std::to_string(r.allocations)+"}";
        }
        out+="],\"traceEvents\":["; first=true;
        for (auto &ev:events) {
            if (!first) out+=','; first=false;
            out+="{\"name\":"+notebookJson(ev.fn->mangledName)+",\"cat\":\"daslang\",\"ph\":\"X\",\"pid\":1,\"tid\":1,\"ts\":"
                +std::to_string(double(ev.start)/1000.)+",\"dur\":"+std::to_string(double(ev.duration)/1000.)+"}";
        }
        return out+="],\"truncated\":"+std::string(truncated?"true":"false")+",\"incomplete_frames\":"+std::to_string(stack.size())+"}";
    }
};

static das::smart_ptr<NotebookProfiler> notebookProfiler;
extern "C" __declspec(dllexport) void nb_profile_start(void *context) {
    auto ctx=static_cast<das::Context *>(context);
    notebookProfiler=das::make_smart<NotebookProfiler>(ctx);
    das::installDebugAgent(notebookProfiler,"jupyter-profiler",nullptr,ctx);
    ctx->instrumentFunction(nullptr,true,0,false);
    ctx->instrumentAllocations=true;
}
extern "C" __declspec(dllexport) const char *nb_profile_stop(void *context) {
    static thread_local std::string result;
    auto ctx=static_cast<das::Context *>(context);
    result=notebookProfiler->json();
    ctx->instrumentAllocations=notebookProfiler->previousAllocations;
    ctx->clearInstruments();
    das::uninstallCppDebugAgent("jupyter-profiler");
    notebookProfiler.reset();
    return result.c_str();
}

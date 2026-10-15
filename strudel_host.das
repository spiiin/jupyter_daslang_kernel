options gen2
options indenting = 4
options persistent_heap
options gc
options multiple_contexts
require daslib/ast
require daslib/debugger
require daslib/jobque_boost
require strudel/strudel
require strudel/strudel_player
require audio/audio_boost
require live/audio_live
require live/live_commands
require live/live_api
require live_host
require strings
require daslib/json
require strings
require daslib/json_boost

// One permanent scheduler, PCM stream and sample clock. Compiled contexts only
// produce Hap data; they never create an audio system or a player.
struct DjnbPacket { haps : array<Hap> }
[safe_when_uninitialized]
struct DjnbVersion {
    ctx : smart_ptr<Context>
    program : ProgramPtr
    name : string
    revision : string
    obsolete : bool = false
    retire_after : int = 0
}
struct DjnbAudioState {
    current : int = 0
    pending : int = 0
    boundary : double = 0.0lf
    waiting : bool = false
    swaps : int = 0
    queries : int = 0
    last_start : double = 0.0lf
    last_stop : double = 0.0lf
    events : int = 0
    rewinds : int = 0
    max_gap : double = 0.0lf
    last_gain : float = 0.0
}
struct DjnbSlot {
    state : DjnbAudioState = DjnbAudioState()
    current_ptr : uint64 = 0ul
    pending_ptr : uint64 = 0ul
}
struct DjnbAudioCommand {
    name : string
    id : int = 0
    ptr : uint64 = 0ul
    serial : int = 0
}
struct DjnbAudioBatch {
    items : array<DjnbAudioCommand>
    serial : int = 0
}
var djnb_slots : table<string; DjnbSlot>
var djnb_versions : array<DjnbVersion>
var djnb_latest : table<string; int>
var djnb_active : table<string; bool>
var djnb_serial : int = 0
var djnb_audio : AudioSystemChannels
var djnb_gc_tick : int64

def djnb_read_state(name : string) : DjnbAudioState {
    var text : string
    if (live_load_string("djnb_part_{name}", text)) {
        var err : string
        var data = read_json(text, err)
        let state = from_JV(data, type<DjnbAudioState>)
        delete_json(data)
        return state
    }
    return DjnbAudioState()
}
def djnb_publish(name : string; state : DjnbAudioState = DjnbAudioState()) {
    var data = JV(state)
    live_store_string("djnb_part_{name}", write_json(data))
    delete_json(data)
}
def djnb_query(ptr : uint64; a,b : double) : array<Hap> {
    var result : array<Hap>
    if (ptr != 0ul && b > a) {
        try {
            with_channel(0) $(ch) {
                let raw = unsafe(reinterpret<Context?>(ptr))
                unsafe(invoke_in_context(raw, "djnb_query", a, b, ch))
                ch |> for_each_clone() $(packet : DjnbPacket#) {
                    result := packet.haps
                }
            }
        } recover {
            live_store_string("djnb_runtime_error", "pattern query failed")
        }
    }
    return <- result
}
def djnb_pattern(name : string) : Pattern {
    return @capture(= name) (span : TimeSpan) : array<Hap> {
        var result : array<Hap>
        var slot & = djnb_slots[name]
        if (slot.state.waiting && span.stop > slot.state.boundary) {
            // Split a scheduler query crossing the commit boundary. No change
            // to the scheduler's startTime, lastQueryEnd, voices or effect tails.
            if (span.start < slot.state.boundary) {
                result <- djnb_query(slot.current_ptr, span.start, slot.state.boundary)
            }
            var next <- djnb_query(slot.pending_ptr, max(span.start, slot.state.boundary), span.stop)
            for (h in next) { result |> push_clone(h) }
            slot.current_ptr = slot.pending_ptr
            slot.state.current = slot.state.pending
            slot.pending_ptr = 0ul
            slot.state.pending = 0
            slot.state.waiting = false
            slot.state.swaps ++
        } else {
            result <- djnb_query(slot.current_ptr, span.start, span.stop)
        }
        if (slot.state.queries > 0) {
            if (span.start < slot.state.last_start) { slot.state.rewinds ++ }
            slot.state.max_gap = max(slot.state.max_gap, span.start - slot.state.last_stop)
        }
        slot.state.queries ++
        slot.state.last_start = span.start
        slot.state.last_stop = span.stop
        slot.state.events += length(result)
        if (!empty(result)) { slot.state.last_gain = result[0].value.gain }
        djnb_publish(name, slot.state)
        return <- result
    }
}
def djnb_stage(cmd : DjnbAudioCommand; boundary : double) {
    if (!key_exists(djnb_slots, cmd.name)) {
        djnb_slots[cmd.name] := DjnbSlot()
        strudel_add_track(djnb_pattern(cmd.name))
    }
    var slot & = djnb_slots[cmd.name]
    slot.pending_ptr = cmd.ptr
    slot.state.pending = cmd.id
    // The normal lookahead is 0.05 cycles at 120 BPM. Leave a quarter
    // cycle of headroom so already-scheduled onsets cannot straddle a swap.
    slot.state.boundary = boundary
    slot.state.waiting = true
    djnb_publish(cmd.name, slot.state)
}
def djnb_dispatch(text : string) {
    var err : string
    var data = read_json(text, err)
    let batch <- from_JV(data, type<DjnbAudioBatch>)
    delete_json(data)
    let boundary = floor(g_wall_time * g_cps + 0.25lf) + 1.0lf
    for (cmd in batch.items) { djnb_stage(cmd, boundary) }
    live_store_string("djnb_ack", "{batch.serial}")
}
def djnb_music() {
    strudel_set_bpm(120.0lf)
    strudel_play(@@djnb_dispatch)
}
def djnb_command(name : string; id : int) : DjnbAudioCommand {
    var ptr = 0ul
    if (id > 0) { ptr = unsafe(reinterpret<uint64>(djnb_versions[id-1].ctx)) }
    return DjnbAudioCommand(name=name, id=id, ptr=ptr)
}
def djnb_send_commands(var items : array<DjnbAudioCommand>) {
    if (empty(items)) { return }
    djnb_serial ++
    var msg = JV(DjnbAudioBatch(items <- items, serial=djnb_serial))
    strudel_command(write_json(msg))
    delete_json(msg)
}
def djnb_send(name : string; id : int) {
    var items : array<DjnbAudioCommand>
    items |> push(djnb_command(name, id))
    djnb_send_commands(items)
}
struct DjnbRequest {
    token : string
    name : string
    path : string
    revision : string
    ids : array<int>
}
def djnb_valid(req : DjnbRequest) : bool { return req.token == "@TOKEN@" }

[live_command(description="Compile pattern code without reloading audio")]
def djnb_compile(input : JsonValue?) : JsonValue? {
    let req <- from_JV(input, type<DjnbRequest>)
    if (!djnb_valid(req)) { return JV("invalid session") }
    var failure : string
    var inscope compiled = DjnbVersion()
    compiled.name = req.name
    compiled.revision = req.revision
    var inscope access <- make_file_access("")
    using() $(var mg : ModuleGroup) {
        using() $(var cop : CodeOfPolicies) {
            cop.threadlock_context = true
            compile_file(req.path, access, unsafe(addr(mg)), cop) $(ok, program, issues) {
                if (!ok) { failure = string(issues); return }
                simulate(program) $(sok; context; serrors) {
                    if (!sok) { failure = string(serrors); return }
                    try {
                        unsafe(invoke_in_context(context, "djnb_build"))
                        compiled.ctx := context
                        compiled.program := program
                    } recover { failure = "Pattern function raised an exception during construction" }
                }
            }
        }
    }
    if (!empty(failure)) { return JV(failure) }
    djnb_versions |> emplace(compiled)
    return JV(length(djnb_versions))
}
[live_command(description="Commit compiled named patterns")]
def djnb_commit(input : JsonValue?) : JsonValue? {
    let req <- from_JV(input, type<DjnbRequest>)
    if (!djnb_valid(req)) { return JV("invalid session") }
    var commands : array<DjnbAudioCommand>
    var retired : array<int>
    for (id in req.ids) {
        if (id < 1 || id > length(djnb_versions) || djnb_versions[id-1].ctx == null) { return JV("invalid version") }
    }
    for (id in req.ids) {
        let name = djnb_versions[id-1].name
        let old = djnb_latest?[name] ?? 0
        djnb_latest[name] = id
        if (djnb_active?[name] ?? false) { commands |> push(djnb_command(name, id)) }
        if (old > 0) {
            djnb_versions[old-1].obsolete = true
            retired |> push(old)
        }
    }
    djnb_send_commands(commands)
    for (id in retired) { djnb_versions[id-1].retire_after = djnb_serial }
    return JV(true)
}
[live_command(description="Discard failed transaction's staged contexts")]
def djnb_discard(input : JsonValue?) : JsonValue? {
    let req <- from_JV(input, type<DjnbRequest>)
    if (!djnb_valid(req)) { return JV(false) }
    for (id in req.ids) {
        if (id > 0 && id <= length(djnb_versions)) {
            djnb_versions[id-1].obsolete = true
            djnb_versions[id-1].retire_after = djnb_serial
        }
    }
    return JV(true)
}
[live_command(description="Start or replace a named pattern at the common cycle boundary")]
def djnb_call(input : JsonValue?) : JsonValue? {
    let req <- from_JV(input, type<DjnbRequest>)
    if (!djnb_valid(req)) { return JV("invalid session") }
    let id = djnb_latest?[req.name] ?? 0
    if (id == 0) { return JV("unknown pattern") }
    djnb_active[req.name] = true
    djnb_send(req.name, id)
    return JV("queued: {req.name}")
}
[live_command(description="Stop one named pattern, retaining its function")]
def djnb_stop(input : JsonValue?) : JsonValue? {
    let req <- from_JV(input, type<DjnbRequest>)
    if (!djnb_valid(req)) { return JV("invalid session") }
    if (!(djnb_active?[req.name] ?? false)) { return JV("already stopped: {req.name}") }
    djnb_active[req.name] = false
    djnb_send(req.name, 0)
    return JV("stop queued: {req.name}")
}
struct DjnbState {
    cycle : double
    seconds : double
    cps : double
    parts : table<string; DjnbAudioState>
    retained_contexts : int
}
[live_command(description="Shared transport and per-pattern diagnostics"), unused_argument(input)]
def djnb_state(input : JsonValue?) : JsonValue? {
    let pt = strudel_get_playback_time()
    var state = DjnbState(cycle=pt._0*pt._1, seconds=pt._0, cps=pt._1)
    for (name in keys(djnb_latest)) { state.parts[name] = djnb_read_state(name) }
    for (v in djnb_versions) { if (v.ctx != null) { state.retained_contexts ++ } }
    return JV(state)
}
[live_command(description="Notebook session"), unused_argument(input)]
def djnb_ready(input : JsonValue?) : JsonValue? { return JV("@TOKEN@") }
[export]
def init() {
    djnb_audio = audio_live_system()
    @SF2@
    strudel_init(@@djnb_music)
    djnb_gc_tick = ref_time_ticks()
}
[export]
def update() {
    if (get_time_usec(djnb_gc_tick) < 100000) { return }
    djnb_gc_tick = ref_time_ticks()
    var text : string
    var ack = 0
    if (live_load_string("djnb_ack", text)) { ack = to_int(text, false) }
    for (v, i in djnb_versions, range(length(djnb_versions))) {
        if (v.obsolete && v.ctx != null && ack >= v.retire_after) {
            let state = djnb_read_state(v.name)
            if (state.current != i+1 && state.pending != i+1) {
                v.ctx := null
                delete v.program
            }
        }
    }
}
[export]
def shutdown() {
    strudel_shutdown()
    audio_live_finalize(djnb_audio)
    for (v in djnb_versions) { v.ctx := null; delete v.program }
    delete djnb_versions
}

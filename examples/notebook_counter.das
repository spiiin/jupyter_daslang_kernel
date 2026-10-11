options gen2
options indenting = 4
module notebook_counter shared

var private count = 0
var private starts = 0
var private values : array<int>
var private labels : table<string; int>

[init]
def startup() {
    starts += 1
    count = 10
    print("module init\n")
}

[finalize]
def cleanup() {
    print("module finalize: count={count}\n")
}

//! Increments the private module counter and remembers its history.
def public next() : int {
    count += 1
    values |> push(count)
    labels["last"] = count
    return count
}

//! Shows module state without changing it.
def public state() : string {
    return "count={count}, starts={starts}, values={values}, labels={labels}"
}

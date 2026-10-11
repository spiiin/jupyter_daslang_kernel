import test from 'node:test';
import assert from 'node:assert/strict';
import { StringStream, StreamLanguage } from '@codemirror/language';
import { highlightTree, classHighlighter } from '@lezer/highlight';
import { daslang } from '../lib/mode.js';

function scan(code, state = daslang.startState()) {
  const tokens = [];
  for (const line of code.split('\n')) {
    const stream = new StringStream(line, 4, 4);
    while (!stream.eol()) {
      stream.start = stream.pos;
      const style = daslang.token(stream, state);
      assert.ok(stream.pos > stream.start, 'Every token must consume input');
      tokens.push([stream.current(), style]);
    }
  }
  return { tokens, state };
}

test('declarations, current vector types, scalar atoms and field paths', () => {
  const { tokens } = scan('struct Stats {count:int}\ndef calc_primes(n:int; var a:array<int>) {\n let v:half8\n if (true) {stats.count += 3}\n}');
  for (const token of [['struct', 'keyword'], ['Stats', 'type'], ['def', 'keyword'],
    ['calc_primes', 'def'], ['array', 'type'], ['half8', 'type'], ['true', 'atom'],
    ['count', 'property'], ['+=', 'operator'], ['3', 'number']]) {
    assert.ok(tokens.some(t => t[0] === token[0] && t[1] === token[1]), JSON.stringify(token));
  }
});

test('numeric literals, range separators, operators and notebook magics', () => {
  const { tokens } = scan('%eval x\n%reset\n0x2aUL + .5f + 1e-3 + 42u8\n1..10\na |> push(3)\nvar gen <- generator<int>() <| $() {}');
  for (const word of ['0x2aUL', '.5f', '1e-3', '42u8']) {
    assert.deepEqual(tokens.find(t => t[0] === word), [word, 'number']);
  }
  for (const word of ['..', '|>', '<-', '<|']) assert.deepEqual(tokens.find(t => t[0] === word), [word, 'operator']);
  assert.deepEqual(tokens[0], ['%eval', 'meta']);
  assert.deepEqual(tokens.find(t => t[0] === '%reset'), ['%reset', 'meta']);
});

test('nested multiline comments do not consume following code', () => {
  const { tokens, state } = scan('/* outer\n/* nested */\n*/ var x = 7 // end');
  assert.equal(state.frames.length, 0);
  assert.deepEqual(tokens.find(t => t[0] === 'var'), ['var', 'keyword']);
  assert.deepEqual(tokens.at(-1), ['// end', 'comment']);
  assert.ok(tokens.filter(t => /outer|nested/.test(t[0])).every(t => t[1] === 'comment'));
});

test('strings, escaped braces, nested interpolation, expression braces and comments', () => {
  const { tokens, state } = scan('print("Привет \\{plain} {foo({x=3}, "inner {n}") /* comment */}")\nvar after = 1');
  assert.equal(state.frames.length, 0);
  assert.deepEqual(tokens.find(t => t[0] === 'foo'), ['foo', 'variable']);
  assert.deepEqual(tokens.find(t => t[0] === 'n'), ['n', 'variable']);
  assert.deepEqual(tokens.find(t => t[0] === 'var'), ['var', 'keyword']);
  assert.ok(tokens.some(t => t[0].includes('plain') && t[1] === 'string'));
  assert.ok(tokens.some(t => t[0].includes('Привет') && t[1] === 'string'));
});

test('copyState isolates nested parser frames between editors and edits', () => {
  const { state } = scan('"text {foo(/* unfinished');
  const clone = daslang.copyState(state);
  scan('*/ 1)}"', clone);
  assert.equal(clone.frames.length, 0);
  assert.equal(state.frames.length, 3);
  assert.equal(state.frames.at(-1).depth, 1);
});

test('CodeMirror emits actual highlight spans for a complete cell', () => {
  const code = '// primes\ndef f(n:int) { return "value {n + 42}" }';
  const language = StreamLanguage.define(daslang);
  const spans = [];
  highlightTree(language.parser.parse(code), classHighlighter, (from, to, cls) => {
    spans.push([code.slice(from, to), cls]);
  });
  assert.ok(spans.some(([text, cls]) => text.includes('def') && cls.includes('tok-keyword')));
  assert.ok(spans.some(([text, cls]) => text.includes('42') && cls.includes('tok-number')));
  assert.ok(spans.some(([text, cls]) => text.includes('value') && cls.includes('tok-string')));
  assert.ok(spans.some(([text, cls]) => text.includes('// primes') && cls.includes('tok-comment')));
});

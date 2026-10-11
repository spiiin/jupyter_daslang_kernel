import type { StreamParser, StringStream } from '@codemirror/language';
import { keywords, types, atoms } from './keywords.js';

type Frame = { kind: 'string' | 'interpolation' | 'comment'; depth: number };
export interface State {
  frames: Frame[];
  declaration: 'function' | 'type' | null;
  property: boolean;
}

// Each editor keeps its own state. Frames also preserve nested strings,
// interpolation expressions and nested block comments across line boundaries.
export const daslang: StreamParser<State> = {
  name: 'daslang',
  startState: () => ({ frames: [], declaration: null, property: false }),
  copyState: state => ({ ...state, frames: state.frames.map(frame => ({ ...frame })) }),
  languageData: {
    commentTokens: { line: '//', block: { open: '/*', close: '*/' } },
    closeBrackets: { brackets: ['(', '[', '{', '"', "'"] }
  },
  token(stream: StringStream, state: State): string | null {
    const frame = state.frames[state.frames.length - 1];
    if (frame?.kind === 'comment') {
      if (stream.match('/*')) frame.depth++;
      else if (stream.match('*/')) {
        if (--frame.depth === 0) state.frames.pop();
      } else {
        stream.next();
        stream.eatWhile(/[^*/]/);
      }
      return 'comment';
    }
    if (frame?.kind === 'string') {
      if (stream.match('\\')) {
        stream.next();
      } else if (stream.match('"')) {
        state.frames.pop();
      } else if (stream.match('{')) {
        state.frames.push({ kind: 'interpolation', depth: 1 });
        return 'string-2';
      } else {
        stream.next();
        stream.eatWhile(/[^\\"{]/);
      }
      return 'string';
    }
    if (stream.eatSpace()) return null;
    if (stream.match('//')) {
      stream.skipToEnd();
      return 'comment';
    }
    if (stream.match('/*')) {
      state.frames.push({ kind: 'comment', depth: 1 });
      return 'comment';
    }
    if (stream.match('"')) {
      state.frames.push({ kind: 'string', depth: 0 });
      state.property = false;
      return 'string';
    }
    if (stream.match(/^'(?:\\.|[^'\\])'(?:u8|U8|u|U)?/)) return 'string';
    if (stream.match(/^0[xX][\da-fA-F][\da-fA-F_]*(?:u8|U8|ul|UL|u|U|l|L)?/)
        || stream.match(/^(?:\d[\d_]*\.\d[\d_]*(?:[eE][+-]?\d+)?|\.\d[\d_]*(?:[eE][+-]?\d+)?|\d[\d_]*[eE][+-]?\d+)(?:lf|d|f|F)?/)
        || stream.match(/^\d[\d_]*(?:lf|d|f|F|u8|U8|ul|UL|u|U|l|L)?/)) {
      state.property = false;
      return 'number';
    }
    if (stream.match(/^[a-zA-Z_][a-zA-Z0-9_]*/)) {
      const word = stream.current();
      if (keywords.has(word)) {
        state.declaration = word === 'def' ? 'function'
          : ['struct', 'class', 'enum', 'typedef'].includes(word) ? 'type' : null;
        state.property = false;
        return 'keyword';
      }
      if (types.has(word)) return 'type';
      if (atoms.has(word)) return 'atom';
      const style = state.declaration === 'function' ? 'def'
        : state.declaration === 'type' ? 'type' : state.property ? 'property' : 'variable';
      state.declaration = null;
      state.property = false;
      return style;
    }
    if (stream.sol() && stream.match(/^%(?:eval|reset)\b/)) return 'meta';
    if (stream.match(/^(?:!\?\[|\?\[|!\[|!\?\.|\?\.|!\.|::|->)/)) {
      state.property = /(?:\.|::|->)$/.test(stream.current());
      return 'operator';
    }
    if (stream.match(/^(?:<<<=|>>>|<<<|\^\^=|\|\|=|&&=|<<=|>>=|\|\||&&|\^\^|<<|>>|<-|<\||\|>|=>|\+\+|--|==|!=|<=|>=|\+=|-=|\*=|\/=|%=|\^=|&=|\|=|@@|!\?\?|\?\?|:=|\.\.)/)) {
      state.property = false;
      return 'operator';
    }
    const ch = stream.next();
    if (frame?.kind === 'interpolation') {
      if (ch === '{') frame.depth++;
      if (ch === '}' && --frame.depth === 0) {
        state.frames.pop();
        return 'string-2';
      }
    }
    state.property = ch === '.';
    state.declaration = null;
    return ch && /[+\-*\/%=<>!&|^~@$:;,.()[\]{}?#]/.test(ch) ? 'operator' : null;
  }
};

import type { JupyterFrontEnd, JupyterFrontEndPlugin } from '@jupyterlab/application';
import { IEditorLanguageRegistry } from '@jupyterlab/codemirror';
import { HighlightStyle, LanguageSupport, StreamLanguage, syntaxHighlighting } from '@codemirror/language';
import { tags } from '@lezer/highlight';
import { daslang } from './mode.js';

const plugin: JupyterFrontEndPlugin<void> = {
  id: 'jupyterlab-daslang:language',
  autoStart: true,
  requires: [IEditorLanguageRegistry],
  activate: (_app: JupyterFrontEnd, languages: IEditorLanguageRegistry) => {
    const language = StreamLanguage.define(daslang);
    // JupyterLab 4.2's default highlighter has no typeName rule. Keep this
    // supplement scoped to daslang and use the active theme's builtin color.
    const typeStyle = HighlightStyle.define([
      { tag: tags.typeName, color: 'var(--jp-mirror-editor-builtin-color)' }
    ], { scope: language });
    languages.addLanguage({
      name: 'daslang',
      displayName: 'daslang',
      alias: ['dascript', 'daScript'],
      mime: ['text/x-dascript', 'text/x-daslang'],
      extensions: ['das'],
      support: new LanguageSupport(language, syntaxHighlighting(typeStyle))
    });
  }
};

export default plugin;

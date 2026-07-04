import * as vscode from 'vscode';
import { LayerGitCli } from './cli';
import { ComposedTreeProvider } from './composedTreeView';
import { LayersProvider } from './layersView';
import { registerCommands } from './commands';

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel('LayerGit');
  output.appendLine('LayerGit extension activated');
  const cli = new LayerGitCli(output, context.extensionUri);
  const layers = new LayersProvider(cli);
  const composedTree = new ComposedTreeProvider(cli);

  context.subscriptions.push(
    output,
    vscode.window.registerTreeDataProvider('layergit.layers', layers),
    vscode.window.registerTreeDataProvider('layergit.composedTree', composedTree)
  );

  registerCommands(context, cli, [layers, composedTree]);
}

export function deactivate(): void {}

import * as vscode from 'vscode';
import { LayerGitCli } from './cli';
import { ComposedTreeProvider } from './composedTreeView';
import { LayersDragAndDropController, LayersProvider } from './layersView';
import { registerCommands } from './commands';

export function activate(context: vscode.ExtensionContext): void {
  const output = vscode.window.createOutputChannel('LayerGit');
  output.appendLine('LayerGit extension activated');
  const cli = new LayerGitCli(output, context.extensionUri);
  const layers = new LayersProvider(cli);
  const composedTree = new ComposedTreeProvider(cli);
  const refresh = () => {
    layers.refresh();
    composedTree.refresh();
  };

  context.subscriptions.push(
    output,
    vscode.window.createTreeView('layergit.layers', {
      treeDataProvider: layers,
      dragAndDropController: new LayersDragAndDropController(cli, refresh),
    }),
    vscode.window.createTreeView('layergit.composedTree', {
      treeDataProvider: composedTree,
      canSelectMany: true,
    })
  );

  registerCommands(context, cli, [layers, composedTree]);
}

export function deactivate(): void {}

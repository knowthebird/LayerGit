import * as vscode from 'vscode';
import { LayerGitCli } from './cli';
import { LayerInfo } from './models';
import { findLayerGitWorkspace } from './workspace';

export class LayersProvider implements vscode.TreeDataProvider<LayerItem | EmptyItem> {
  private readonly changed = new vscode.EventEmitter<LayerItem | EmptyItem | undefined>();
  readonly onDidChangeTreeData = this.changed.event;

  constructor(private readonly cli: LayerGitCli) {}

  refresh(): void {
    this.changed.fire(undefined);
  }

  getTreeItem(element: LayerItem | EmptyItem): vscode.TreeItem {
    return element;
  }

  async getChildren(): Promise<Array<LayerItem | EmptyItem>> {
    const workspace = await findLayerGitWorkspace();
    if (!workspace) {
      return [new EmptyItem('No LayerGit workspace found')];
    }

    try {
      const status = await this.cli.status(workspace);
      return [...status.layers]
        .reverse()
        .map((layer) => new LayerItem(layer, status.write_layer === layer.name));
    } catch (error) {
      return [new EmptyItem(error instanceof Error ? error.message : String(error))];
    }
  }
}

export class LayerItem extends vscode.TreeItem {
  constructor(readonly layer: LayerInfo, readonly writeLayer: boolean) {
    const state = layer.enabled ? 'enabled' : 'disabled';
    const branch = layer.branch ?? '-';
    const top = layer.position === 'top' ? ' top' : '';
    const write = writeLayer ? ' write' : '';
    const kind = layer.kind ?? 'git';
    super(`${layer.index} ${layer.name}`, vscode.TreeItemCollapsibleState.None);
    this.description = `${kind} ${state} ${branch} ${layer.status}${top}${write}`;
    this.contextValue = layer.enabled ? 'layergit.layer.enabled' : 'layergit.layer.disabled';
    this.iconPath = new vscode.ThemeIcon(layer.enabled ? 'layers-active' : 'circle-slash');
    this.tooltip = `${layer.name}\n${kind}\n${state}${write ? '\nwrite layer' : ''}\n${layer.repo ?? ''}`;
  }
}

class EmptyItem extends vscode.TreeItem {
  constructor(message: string) {
    super(message, vscode.TreeItemCollapsibleState.None);
    this.contextValue = 'layergit.empty';
    this.iconPath = new vscode.ThemeIcon('info');
  }
}

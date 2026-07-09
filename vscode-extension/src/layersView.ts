import * as vscode from 'vscode';
import { LayerGitCli } from './cli';
import { LayerInfo } from './models';
import { findLayerGitWorkspace } from './workspace';

export type LayersNode = LayerItem | MessageItem | ActionItem | SectionItem;

const LAYER_MIME = 'application/vnd.code.tree.layergit.layer';

export class LayersProvider implements vscode.TreeDataProvider<LayersNode> {
  private readonly changed = new vscode.EventEmitter<LayersNode | undefined>();
  readonly onDidChangeTreeData = this.changed.event;

  constructor(private readonly cli: LayerGitCli) {}

  refresh(): void {
    this.changed.fire(undefined);
  }

  getTreeItem(element: LayersNode): vscode.TreeItem {
    return element;
  }

  async getChildren(): Promise<LayersNode[]> {
    const workspace = await findLayerGitWorkspace();
    if (!workspace) {
      await vscode.commands.executeCommand('setContext', 'layergit.workspaceFound', false);
      return [
        new MessageItem(
          'No LayerGit workspace found',
          'Initialize a workspace to start composing Git repositories into buildtree/.'
        ),
        new ActionItem('Initialize LayerGit Workspace', 'layergit.init', 'repo-create', 'layergit.action.initialize'),
      ];
    }

    await vscode.commands.executeCommand('setContext', 'layergit.workspaceFound', true);
    try {
      const status = await this.cli.status(workspace);
      const layers = [...status.layers]
        .reverse()
        .map((layer) => new LayerItem(layer, status.write_layer === layer.name));
      const actions: LayersNode[] = [
        new ActionItem('Add Layer...', 'layergit.addLayer', 'add', 'layergit.action.workspace'),
      ];
      if (layers.length) {
        actions.push(new ActionItem('Remove Layer...', 'layergit.removeLayer', 'remove', 'layergit.action.workspace'));
        actions.push(new ActionItem('Apply All Changes', 'layergit.applyAll', 'check', 'layergit.action.workspace'));
      }
      actions.push(
        new ActionItem('Refresh Layers', 'layergit.refresh', 'refresh', 'layergit.action.workspace'),
        new ActionItem('Open LayerGit Output', 'layergit.openOutput', 'output', 'layergit.action.workspace'),
        new SectionItem('---------------- Layers ----------------'),
      );
      return [...actions, ...layers];
    } catch (error) {
      return [new MessageItem(error instanceof Error ? error.message : String(error))];
    }
  }
}

export class LayersDragAndDropController implements vscode.TreeDragAndDropController<LayersNode> {
  readonly dragMimeTypes = [LAYER_MIME];
  readonly dropMimeTypes = [LAYER_MIME];

  constructor(
    private readonly cli: LayerGitCli,
    private readonly refresh: () => void
  ) {}

  handleDrag(source: readonly LayersNode[], dataTransfer: vscode.DataTransfer): void {
    const layer = source.find((item): item is LayerItem => item instanceof LayerItem);
    if (!layer) {
      return;
    }
    dataTransfer.set(LAYER_MIME, new vscode.DataTransferItem(layer.layer.name));
  }

  async handleDrop(target: LayersNode | undefined, dataTransfer: vscode.DataTransfer): Promise<void> {
    const item = dataTransfer.get(LAYER_MIME);
    const draggedLayer = item?.value;
    if (typeof draggedLayer !== 'string') {
      return;
    }
    if (target && !(target instanceof LayerItem)) {
      return;
    }
    if (target instanceof LayerItem && target.layer.name === draggedLayer) {
      return;
    }

    const workspace = await findLayerGitWorkspace();
    if (!workspace) {
      vscode.window.showWarningMessage('No LayerGit workspace found in this VS Code folder.');
      return;
    }

    const args = target
      ? ['move', draggedLayer, 'after', target.layer.name]
      : ['move', draggedLayer, 'top'];
    const destination = target ? `above ${target.layer.name}` : 'to the top';
    try {
      await this.cli.run(workspace, args);
      vscode.window.showInformationMessage(`Moved ${draggedLayer} ${destination}.`);
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      vscode.window.showErrorMessage(message);
    } finally {
      this.refresh();
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
    const mount = layer.mount ?? '/';
    super(`${layer.index} ${layer.name}`, vscode.TreeItemCollapsibleState.None);
    this.description = `${kind} ${mount} ${state} ${branch} ${layer.status}${top}${write}`;
    this.contextValue = layer.enabled ? 'layergit.layer.enabled' : 'layergit.layer.disabled';
    this.iconPath = new vscode.ThemeIcon(layer.enabled ? 'layers-active' : 'circle-slash');
    this.tooltip = `${layer.name}\n${kind}\nmount: ${mount}\n${state}${write ? '\nwrite layer' : ''}\nDrop another layer here to move it above ${layer.name}.\n${layer.repo ?? ''}`;
  }
}

class MessageItem extends vscode.TreeItem {
  constructor(message: string, detail?: string) {
    super(message, vscode.TreeItemCollapsibleState.None);
    this.contextValue = 'layergit.message';
    this.description = detail;
    this.iconPath = new vscode.ThemeIcon('info');
  }
}

class ActionItem extends vscode.TreeItem {
  constructor(label: string, command: string, icon: string, contextValue: string) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.contextValue = contextValue;
    this.iconPath = new vscode.ThemeIcon(icon);
    this.command = { command, title: label };
  }
}

class SectionItem extends vscode.TreeItem {
  constructor(label: string) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.contextValue = 'layergit.section';
    this.iconPath = new vscode.ThemeIcon('dash');
  }
}

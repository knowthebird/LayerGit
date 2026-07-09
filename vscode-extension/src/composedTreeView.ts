import * as path from 'path';
import * as vscode from 'vscode';
import { LayerGitCli } from './cli';
import { TreeFile } from './models';
import { exists, findLayerGitWorkspace } from './workspace';

type Node = DirectoryNode | FileNode | MessageNode | ActionNode | SectionNode;

export class ComposedTreeProvider implements vscode.TreeDataProvider<Node> {
  private readonly changed = new vscode.EventEmitter<Node | undefined>();
  readonly onDidChangeTreeData = this.changed.event;
  private roots: DirectoryNode[] | undefined;

  constructor(private readonly cli: LayerGitCli) {}

  refresh(): void {
    this.roots = undefined;
    this.changed.fire(undefined);
  }

  getTreeItem(element: Node): vscode.TreeItem {
    return element;
  }

  async getChildren(element?: Node): Promise<Node[]> {
    if (element instanceof DirectoryNode) {
      return element.children;
    }
    if (element) {
      return [];
    }

    const workspace = await findLayerGitWorkspace();
    if (!workspace) {
      return [
        new MessageNode(
          'No LayerGit workspace found',
          'Initialize a workspace from the Layers view.'
        ),
      ];
    }

    try {
      const tree = await this.cli.tree(workspace);
      this.roots = buildTree(workspace, tree.output, tree.files);
      const stale = tree.files.some((file) => file.ownership === 'stale');
      const actions = [
        new ActionNode('Compose / Refresh Tree', 'layergit.compose', 'sync'),
        new ActionNode('New File...', 'layergit.newFile', 'new-file'),
        new ActionNode('Apply All Changes', 'layergit.applyAll', 'check'),
      ];
      if (!this.roots.length) {
        const outputExists = await exists(outputUri(workspace, tree.output));
        return outputExists
          ? [...actions, new MessageNode('Composed tree is empty')]
          : [
              new MessageNode(
                'No composed tree found',
                `Run layer compose to generate ${tree.output || 'buildtree/'}.`
              ),
              actions[0],
            ];
      }
      return stale
        ? [
            new MessageNode('Generated state may be stale', 'Run layer compose.', 'warning'),
            ...actions,
            new SectionNode('---------------- Files ----------------'),
            ...this.roots,
          ]
        : [...actions, new SectionNode('---------------- Files ----------------'), ...this.roots];
    } catch (error) {
      return [new MessageNode(error instanceof Error ? error.message : String(error), undefined, 'error')];
    }
  }
}

export class DirectoryNode extends vscode.TreeItem {
  readonly children: Node[] = [];
  hidden = false;

  constructor(readonly name: string, readonly relativePath: string) {
    super(name, vscode.TreeItemCollapsibleState.Collapsed);
    this.contextValue = 'layergit.directory';
    this.iconPath = new vscode.ThemeIcon('folder');
  }

  files(): FileNode[] {
    const result: FileNode[] = [];
    for (const child of this.children) {
      if (child instanceof FileNode) {
        result.push(child);
      } else if (child instanceof DirectoryNode) {
        result.push(...child.files());
      }
    }
    return result;
  }

  refreshHiddenState(): boolean {
    const files = this.files();
    this.hidden = files.length > 0 && files.every((file) => file.file.hidden);
    this.iconPath = new vscode.ThemeIcon(this.hidden ? 'eye-closed' : 'folder');
    this.description = this.hidden ? 'hidden' : undefined;
    this.tooltip = this.hidden
      ? `${this.relativePath}/\nall files hidden by layer selection`
      : `${this.relativePath}/`;
    return this.hidden;
  }
}

export class FileNode extends vscode.TreeItem {
  constructor(
    readonly workspace: vscode.Uri,
    readonly output: string,
    readonly file: TreeFile
  ) {
    super(path.basename(file.path), vscode.TreeItemCollapsibleState.None);
    this.description = fileDescription(file);
    this.contextValue = 'layergit.file';
    this.iconPath = new vscode.ThemeIcon(fileIcon(file));
    const owner = fileTooltipOwner(file);
    this.tooltip = `${file.path}\n${owner}`;
    if (!file.hidden) {
      this.command = {
        command: 'vscode.open',
        title: 'Open File',
        arguments: [this.uri, { preview: true }],
      };
    }
  }

  get uri(): vscode.Uri {
    return vscode.Uri.joinPath(this.workspace, this.output, ...this.file.path.split('/'));
  }
}

function fileDescription(file: TreeFile): string | undefined {
  if (file.ownership === 'stale') {
    return 'stale owned';
  }
  if (file.ownership === 'untracked') {
    return 'untracked';
  }
  return file.hidden ? `hidden by ${file.selectedLayer ?? 'selection'}` : file.visibleLayer;
}

function fileIcon(file: TreeFile): string {
  if (file.ownership === 'stale') {
    return 'warning';
  }
  if (file.ownership === 'untracked') {
    return 'question';
  }
  return file.hidden ? 'eye-closed' : 'file';
}

function fileTooltipOwner(file: TreeFile): string {
  if (file.ownership === 'stale') {
    return 'previously owned by LayerGit but no longer valid for current layer.yaml';
  }
  if (file.ownership === 'untracked') {
    return 'not owned by any layer';
  }
  return file.hidden
    ? `hidden by selection\nassigned layer: ${file.selectedLayer ?? 'unknown'}\n${file.reason ?? ''}`
    : `visible from: ${file.visibleLayer ?? 'unknown'}`;
}

class MessageNode extends vscode.TreeItem {
  constructor(message: string, detail?: string, icon = 'info') {
    super(message, vscode.TreeItemCollapsibleState.None);
    this.contextValue = 'layergit.message';
    this.description = detail;
    this.iconPath = new vscode.ThemeIcon(icon);
  }
}

class ActionNode extends vscode.TreeItem {
  constructor(label: string, command: string, icon: string) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.contextValue = 'layergit.action.workspace';
    this.iconPath = new vscode.ThemeIcon(icon);
    this.command = { command, title: label };
  }
}

class SectionNode extends vscode.TreeItem {
  constructor(label: string) {
    super(label, vscode.TreeItemCollapsibleState.None);
    this.contextValue = 'layergit.section';
    this.iconPath = new vscode.ThemeIcon('dash');
  }
}

function outputUri(workspace: vscode.Uri, output: string): vscode.Uri {
  const segments = output.replace(/^\.\//, '').split(/[\\/]+/).filter(Boolean);
  return vscode.Uri.joinPath(workspace, ...segments);
}

function buildTree(workspace: vscode.Uri, output: string, files: TreeFile[]): DirectoryNode[] {
  const roots: DirectoryNode[] = [];
  const directories = new Map<string, DirectoryNode>();

  for (const file of files) {
    const parts = file.path.split('/');
    let currentChildren = roots as Node[];
    let currentPath = '';

    for (const part of parts.slice(0, -1)) {
      currentPath = currentPath ? `${currentPath}/${part}` : part;
      let directory = directories.get(currentPath);
      if (!directory) {
        directory = new DirectoryNode(part, currentPath);
        directories.set(currentPath, directory);
        currentChildren.push(directory);
      }
      currentChildren = directory.children;
    }

    currentChildren.push(new FileNode(workspace, output, file));
  }

  sortNodes(roots);
  updateDirectoryHiddenStates(roots);
  return roots;
}

function updateDirectoryHiddenStates(nodes: Node[]): void {
  for (const node of nodes) {
    if (node instanceof DirectoryNode) {
      updateDirectoryHiddenStates(node.children);
      node.refreshHiddenState();
    }
  }
}

function sortNodes(nodes: Node[]): void {
  nodes.sort((left, right) => {
    const leftDir = left instanceof DirectoryNode;
    const rightDir = right instanceof DirectoryNode;
    if (leftDir !== rightDir) {
      return leftDir ? -1 : 1;
    }
    return String(left.label ?? '').localeCompare(String(right.label ?? ''));
  });
  for (const node of nodes) {
    if (node instanceof DirectoryNode) {
      sortNodes(node.children);
    }
  }
}

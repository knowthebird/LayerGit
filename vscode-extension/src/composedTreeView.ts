import * as path from 'path';
import * as vscode from 'vscode';
import { LayerGitCli } from './cli';
import { TreeFile } from './models';
import { findLayerGitWorkspace } from './workspace';

type Node = DirectoryNode | FileNode | EmptyNode;

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
      return [new EmptyNode('No LayerGit workspace found')];
    }

    try {
      const tree = await this.cli.tree(workspace);
      this.roots = buildTree(workspace, tree.output, tree.files);
      return this.roots.length ? this.roots : [new EmptyNode('Composed tree is empty')];
    } catch (error) {
      return [new EmptyNode(error instanceof Error ? error.message : String(error))];
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
    this.command = {
      command: 'vscode.open',
      title: 'Open File',
      arguments: [this.uri, { preview: true }],
    };
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
    ? `hidden by selected layer: ${file.selectedLayer ?? 'unknown'}`
    : `visible from: ${file.visibleLayer ?? 'unknown'}`;
}

class EmptyNode extends vscode.TreeItem {
  constructor(message: string) {
    super(message, vscode.TreeItemCollapsibleState.None);
    this.contextValue = 'layergit.empty';
    this.iconPath = new vscode.ThemeIcon('info');
  }
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

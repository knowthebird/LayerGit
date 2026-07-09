import * as vscode from 'vscode';
import { LayerGitCli } from './cli';
import { DirectoryNode, FileNode } from './composedTreeView';
import { LayerItem } from './layersView';
import { LayerGitStatus, TreeFile } from './models';
import { findLayerGitWorkspace, relativeComposedPath } from './workspace';

export interface Refreshable {
  refresh(): void;
}

export function registerCommands(
  context: vscode.ExtensionContext,
  cli: LayerGitCli,
  providers: Refreshable[]
): void {
  const refresh = () => providers.forEach((provider) => provider.refresh());

  context.subscriptions.push(
    vscode.commands.registerCommand('layergit.refresh', refresh),
    vscode.commands.registerCommand('layergit.init', async () => {
      const workspace = await requireWorkspaceFolder();
      if (!workspace) {
        return;
      }
      if (await runCliAction(cli, workspace, ['init'], refresh)) {
        vscode.window.showInformationMessage('Initialized LayerGit workspace.');
      }
    }),
    vscode.commands.registerCommand('layergit.openOutput', async () => {
      cli.showOutput();
    }),
    vscode.commands.registerCommand('layergit.compose', async () => {
      const workspace = await requireLayerGitWorkspace();
      if (!workspace) {
        return;
      }
      await runCliAction(cli, workspace, ['compose'], refresh);
    }),
    vscode.commands.registerCommand('layergit.pullAll', async () => {
      const workspace = await requireLayerGitWorkspace();
      if (!workspace) {
        return;
      }
      await runCliAction(cli, workspace, ['pull'], refresh);
    }),
    vscode.commands.registerCommand('layergit.addLayer', async () => {
      await addLayer(cli, refresh);
    }),
    vscode.commands.registerCommand('layergit.addLocalLayer', async () => {
      await addLocalLayer(cli, refresh);
    }),
    vscode.commands.registerCommand('layergit.removeLayer', async (node?: LayerItem) => {
      await removeLayer(cli, node, refresh);
    }),
    vscode.commands.registerCommand('layergit.setWriteLayer', async (node?: LayerItem) => {
      await setWriteLayer(cli, node, refresh);
    }),
    vscode.commands.registerCommand('layergit.openLayerCache', async (node?: LayerItem) => {
      await openLayerCache(node);
    }),
    vscode.commands.registerCommand('layergit.gitStatusLayer', async (node?: LayerItem) => {
      await gitStatusLayer(cli, node);
    }),
    vscode.commands.registerCommand('layergit.applyLayer', async (node?: LayerItem) => {
      await applyLayer(cli, node, refresh);
    }),
    vscode.commands.registerCommand('layergit.applyAll', async () => {
      await applyAll(cli, refresh);
    }),
    vscode.commands.registerCommand('layergit.openManifest', async () => {
      const workspace = await requireLayerGitWorkspace();
      if (!workspace) {
        return;
      }
      const document = await vscode.workspace.openTextDocument(vscode.Uri.joinPath(workspace, 'layer.yaml'));
      await vscode.window.showTextDocument(document);
    }),
    vscode.commands.registerCommand('layergit.explainCurrentFile', async () => {
      const workspace = await requireLayerGitWorkspace();
      if (!workspace) {
        return;
      }
      const active = vscode.window.activeTextEditor?.document.uri;
      if (!active) {
        vscode.window.showWarningMessage('No active file to explain.');
        return;
      }
      const status = await cli.status(workspace);
      const rel = relativeComposedPath(workspace, active, status.output);
      if (!rel) {
        vscode.window.showWarningMessage('Active file is not inside the composed output tree.');
        return;
      }
      await explain(cli, workspace, rel);
    }),
    vscode.commands.registerCommand('layergit.explainFile', async (node?: FileNode) => {
      const workspace = await requireLayerGitWorkspace();
      if (!workspace || !node) {
        return;
      }
      await explain(cli, workspace, node.file.path);
    }),
    vscode.commands.registerCommand('layergit.enableLayer', async (node?: LayerItem) => {
      await setLayerEnabled(cli, node, true, refresh);
    }),
    vscode.commands.registerCommand('layergit.disableLayer', async (node?: LayerItem) => {
      await setLayerEnabled(cli, node, false, refresh);
    }),
    vscode.commands.registerCommand('layergit.moveLayerUp', async (node?: LayerItem) => {
      await moveLayer(cli, node, 'up', refresh);
    }),
    vscode.commands.registerCommand('layergit.moveLayerDown', async (node?: LayerItem) => {
      await moveLayer(cli, node, 'down', refresh);
    }),
    vscode.commands.registerCommand('layergit.sendLayerToTop', async (node?: LayerItem) => {
      await moveLayer(cli, node, 'top', refresh);
    }),
    vscode.commands.registerCommand('layergit.sendLayerToBottom', async (node?: LayerItem) => {
      await moveLayer(cli, node, 'bottom', refresh);
    }),
    vscode.commands.registerCommand('layergit.useLayerForFile', async (node?: FileNode, selected?: FileNode[]) => {
      await useLayerForFile(cli, node, selected, refresh);
    }),
    vscode.commands.registerCommand('layergit.useLayerForFolder', async (node?: DirectoryNode) => {
      await useLayerForFolder(cli, node, refresh);
    }),
    vscode.commands.registerCommand('layergit.applyFile', async (node?: FileNode, selected?: FileNode[]) => {
      await applyFile(cli, node, selected, refresh);
    }),
    vscode.commands.registerCommand('layergit.clearSelection', async (node?: FileNode, selected?: FileNode[]) => {
      await clearSelection(cli, node, selected, refresh);
    })
  );
}

async function addLayer(cli: LayerGitCli, refresh: () => void): Promise<void> {
  const picked = await vscode.window.showQuickPick(
    [
      {
        label: 'Add Repo Layer',
        description: 'Clone or link an existing Git repository into the layer stack',
        action: 'repo' as const,
      },
      {
        label: 'Add Local Layer',
        description: 'Create a local Git-backed layer under .layer/cache/',
        action: 'local' as const,
      },
    ],
    {
      title: 'LayerGit: Add Layer',
      placeHolder: 'Choose the kind of layer to add',
    }
  );
  if (!picked) {
    return;
  }
  if (picked.action === 'local') {
    await addLocalLayer(cli, refresh);
    return;
  }
  await addRepoLayer(cli, refresh);
}

async function addRepoLayer(cli: LayerGitCli, refresh: () => void): Promise<void> {
  const workspace = await requireLayerGitWorkspace();
  if (!workspace) {
    return;
  }
  const repo = await vscode.window.showInputBox({
    title: 'LayerGit: Add Layer',
    prompt: 'Repository URL or local path',
  });
  if (!repo) {
    return;
  }
  const name = await vscode.window.showInputBox({
    title: 'LayerGit: Add Layer',
    prompt: 'Optional layer name. Leave blank to infer from repo.',
  });
  const args = ['add', repo];
  if (name) {
    args.push(name);
  }
  await runCliAction(cli, workspace, args, refresh);
}

async function addLocalLayer(cli: LayerGitCli, refresh: () => void): Promise<void> {
  const workspace = await requireLayerGitWorkspace();
  if (!workspace) {
    return;
  }
  const name = await vscode.window.showInputBox({
    title: 'LayerGit: Add Local Layer',
    prompt: 'Layer name for a local Git-backed repo under .layer/cache/',
    validateInput: (value) => (value.trim() ? undefined : 'Layer name is required.'),
  });
  if (!name) {
    return;
  }
  await runCliAction(cli, workspace, ['add', '--local', name.trim()], refresh);
}

async function removeLayer(
  cli: LayerGitCli,
  node: LayerItem | undefined,
  refresh: () => void
): Promise<void> {
  const workspace = await requireLayerGitWorkspace();
  if (!workspace) {
    return;
  }
  const layerName = node?.layer.name ?? (await pickLayerName(cli, workspace, 'LayerGit: Remove Layer', true));
  if (!layerName) {
    return;
  }
  const confirmed = await vscode.window.showWarningMessage(
    `Remove layer ${layerName} from layer.yaml? Cached repo contents are kept by default.`,
    { modal: true },
    'Remove Layer'
  );
  if (confirmed !== 'Remove Layer') {
    return;
  }
  await runCliAction(cli, workspace, ['remove', layerName], refresh);
}

async function setWriteLayer(
  cli: LayerGitCli,
  node: LayerItem | undefined,
  refresh: () => void
): Promise<void> {
  const workspace = await requireLayerGitWorkspace();
  if (!workspace) {
    return;
  }
  const layerName = node?.layer.name ?? (await pickLayerName(cli, workspace, 'LayerGit: Set Write Layer', true));
  if (!layerName) {
    return;
  }
  await runCliAction(cli, workspace, ['write', layerName], refresh);
}

async function openLayerCache(node: LayerItem | undefined): Promise<void> {
  const workspace = await requireLayerGitWorkspace();
  if (!workspace || !node) {
    return;
  }
  const cache = vscode.Uri.joinPath(workspace, '.layer', 'cache', node.layer.name);
  await vscode.commands.executeCommand('vscode.openFolder', cache, { forceNewWindow: true });
}

async function gitStatusLayer(cli: LayerGitCli, node: LayerItem | undefined): Promise<void> {
  const workspace = await requireLayerGitWorkspace();
  if (!workspace || !node) {
    return;
  }
  try {
    const stdout = await cli.run(workspace, ['-L', node.layer.name, 'git', 'status', '--short']);
    cli.showDetails(`LayerGit Git Status: ${node.layer.name}`, [stdout.trim() || 'clean']);
  } catch (error) {
    showError(error);
  }
}

async function applyLayer(
  cli: LayerGitCli,
  node: LayerItem | undefined,
  refresh: () => void
): Promise<void> {
  const workspace = await requireLayerGitWorkspace();
  if (!workspace || !node) {
    return;
  }
  await runCliAction(cli, workspace, ['apply', '--layer', node.layer.name], refresh);
}

async function applyAll(cli: LayerGitCli, refresh: () => void): Promise<void> {
  const workspace = await requireLayerGitWorkspace();
  if (!workspace) {
    return;
  }
  await runCliAction(cli, workspace, ['apply', '--all'], refresh);
}

async function setLayerEnabled(
  cli: LayerGitCli,
  node: LayerItem | undefined,
  enabled: boolean,
  refresh: () => void
): Promise<void> {
  const workspace = await requireLayerGitWorkspace();
  if (!workspace || !node) {
    return;
  }
  await runCliAction(cli, workspace, [enabled ? 'enable' : 'disable', node.layer.name], refresh);
}

async function moveLayer(
  cli: LayerGitCli,
  node: LayerItem | undefined,
  command: string,
  refresh: () => void
): Promise<void> {
  const workspace = await requireLayerGitWorkspace();
  if (!workspace || !node) {
    return;
  }
  await runCliAction(cli, workspace, ['move', node.layer.name, command], refresh);
}

async function useLayerForFile(
  cli: LayerGitCli,
  node: FileNode | undefined,
  selected: FileNode[] | undefined,
  refresh: () => void
): Promise<void> {
  const workspace = await requireLayerGitWorkspace();
  const files = selectedFileNodes(node, selected);
  if (!workspace || !files.length) {
    return;
  }
  const status = await cli.status(workspace);
  const picked = await vscode.window.showQuickPick(
    status.layers.map((layer) => ({
      label: layer.name,
      description: layerDescription(layer, status.write_layer === layer.name),
      detail: layer.repo,
      layer,
    })),
    {
      title: files.length === 1 ? `Use layer for ${files[0].file.path}` : `Use layer for ${files.length} files`,
      placeHolder: 'Select the layer/repo to use for the selected file(s)',
    }
  );
  if (!picked) {
    return;
  }
  if (!picked.layer.enabled) {
    vscode.window.showWarningMessage(`Layer ${picked.layer.name} is disabled. Enable it before selecting it for a file.`);
    return;
  }
  const runAction = files.length === 1
    ? (args: string[]) => runCliAction(cli, workspace, args, refresh)
    : (args: string[]) => runCliActionWithoutRefresh(cli, workspace, args);
  let succeeded = 0;
  for (const file of files) {
    if (await useLayerForOneFile(cli, workspace, status, file, picked.layer.name, runAction)) {
      succeeded += 1;
    }
  }
  if (files.length > 1) {
    refresh();
    showBatchResult('Applied layer selection', succeeded, files.length);
  }
}

type CliAction = (args: string[]) => Promise<boolean>;

async function useLayerForOneFile(
  cli: LayerGitCli,
  workspace: vscode.Uri,
  status: LayerGitStatus,
  node: FileNode,
  layerName: string,
  runAction: CliAction
): Promise<boolean> {
  const targetProvidesFile = layerProvidesFile(node.file, layerName);
  if (hasModifiedFile(status, node.file.path)) {
    return handleDirtyFileSelection(cli, workspace, node.file.path, layerName, targetProvidesFile, runAction);
  }
  if (!targetProvidesFile) {
    return handleMissingProviderSelection(cli, workspace, node.file.path, layerName, Boolean(node.file.hidden), runAction);
  }
  return runAction(['use', node.file.path, layerName]);
}

async function handleDirtyFileSelection(
  cli: LayerGitCli,
  workspace: vscode.Uri,
  filePath: string,
  layerName: string,
  targetProvidesFile: boolean,
  runAction: CliAction
): Promise<boolean> {
  const choice = await vscode.window.showWarningMessage(
    `${filePath} has unapplied buildtree edits. Choose how to handle them before selecting ${layerName}.`,
    { modal: true },
    'Apply to Current Layer',
    'Adopt Edits into Selected Layer',
    'Discard Edits and Switch'
  );
  if (!choice) {
    return false;
  }
  if (choice === 'Adopt Edits into Selected Layer') {
    return runAction(['adopt', filePath, layerName]);
  }
  if (choice === 'Apply to Current Layer') {
    const applied = await runAction(['apply', filePath]);
    if (!applied) {
      return false;
    }
    if (targetProvidesFile) {
      return runAction(['use', filePath, layerName]);
    }
    return handleMissingProviderSelection(cli, workspace, filePath, layerName, false, runAction);
  }
  const composed = await runAction(['compose']);
  if (!composed) {
    return false;
  }
  if (targetProvidesFile) {
    return runAction(['use', filePath, layerName]);
  }
  return handleMissingProviderSelection(cli, workspace, filePath, layerName, false, runAction);
}

async function handleMissingProviderSelection(
  cli: LayerGitCli,
  workspace: vscode.Uri,
  filePath: string,
  layerName: string,
  fileHidden: boolean,
  runAction: CliAction
): Promise<boolean> {
  if (fileHidden) {
    const choice = await vscode.window.showWarningMessage(
      `${filePath} is currently hidden by layer selection. Restore the inherited file before adopting it into ${layerName}.`,
      { modal: true },
      'Restore Then Adopt',
      'Keep Hidden'
    );
    if (choice === 'Restore Then Adopt') {
      const restored = await runAction(['unuse', filePath]);
      if (restored) {
        return runAction(['adopt', filePath, layerName]);
      }
    }
    return false;
  }
  const choice = await vscode.window.showWarningMessage(
    `${layerName} does not currently provide ${filePath}. Choose whether to hide the inherited file or copy the current buildtree file into ${layerName}.`,
    { modal: true },
    'Hide Inherited File',
    'Adopt Current File'
  );
  if (choice === 'Hide Inherited File') {
    return runAction(['use', filePath, layerName, '--hide']);
  }
  if (choice === 'Adopt Current File') {
    return runAction(['adopt', filePath, layerName]);
  }
  return false;
}

async function useLayerForFolder(
  cli: LayerGitCli,
  node: DirectoryNode | undefined,
  refresh: () => void
): Promise<void> {
  const workspace = await requireLayerGitWorkspace();
  if (!workspace || !node) {
    return;
  }
  const files = node.files();
  if (!files.length) {
    vscode.window.showInformationMessage(`No files found under ${node.relativePath}.`);
    return;
  }
  const layerName = await pickLayerName(cli, workspace, `Use layer for ${node.relativePath}/`);
  if (!layerName) {
    return;
  }
  const confirmed = await vscode.window.showWarningMessage(
    `Use layer ${layerName} for ${files.length} file(s) under ${node.relativePath}/? Files not provided by that layer will be skipped and reported.`,
    { modal: true },
    'Apply Recursively'
  );
  if (confirmed !== 'Apply Recursively') {
    return;
  }

  let failures = 0;
  for (const file of files) {
    if (!layerProvidesFile(file.file, layerName)) {
      failures += 1;
      cli.showDetails(`LayerGit folder selection skipped: ${file.file.path}`, [
        `${layerName} does not currently provide ${file.file.path}.`,
        `Use the file context menu to hide or adopt this file explicitly.`,
      ]);
      continue;
    }
    try {
      await cli.run(workspace, ['use', file.file.path, layerName]);
    } catch (error) {
      failures += 1;
      const message = error instanceof Error ? error.message : String(error);
      cli.showDetails(`LayerGit folder selection error: ${file.file.path}`, [message]);
    }
  }
  refresh();
  if (failures) {
    vscode.window.showWarningMessage(`Applied folder layer selection with ${failures} failure(s). See LayerGit output.`);
  }
}

async function applyFile(
  cli: LayerGitCli,
  node: FileNode | undefined,
  selected: FileNode[] | undefined,
  refresh: () => void
): Promise<void> {
  const workspace = await requireLayerGitWorkspace();
  const files = selectedFileNodes(node, selected);
  if (!workspace || !files.length) {
    return;
  }
  if (files.length === 1) {
    await runCliAction(cli, workspace, ['apply', files[0].file.path], refresh);
    return;
  }
  let succeeded = 0;
  for (const file of files) {
    if (await runCliActionWithoutRefresh(cli, workspace, ['apply', file.file.path])) {
      succeeded += 1;
    }
  }
  refresh();
  showBatchResult('Applied buildtree changes', succeeded, files.length);
}

async function clearSelection(
  cli: LayerGitCli,
  node: FileNode | undefined,
  selected: FileNode[] | undefined,
  refresh: () => void
): Promise<void> {
  const workspace = await requireLayerGitWorkspace();
  const files = selectedFileNodes(node, selected);
  if (!workspace || !files.length) {
    return;
  }
  if (files.length === 1) {
    await runCliAction(cli, workspace, ['unuse', files[0].file.path], refresh);
    return;
  }
  let succeeded = 0;
  for (const file of files) {
    if (await runCliActionWithoutRefresh(cli, workspace, ['unuse', file.file.path])) {
      succeeded += 1;
    }
  }
  refresh();
  showBatchResult('Cleared layer selection', succeeded, files.length);
}

async function pickLayerName(
  cli: LayerGitCli,
  workspace: vscode.Uri,
  title: string,
  allowDisabled = false
): Promise<string | undefined> {
  const status = await cli.status(workspace);
  const picked = await vscode.window.showQuickPick(
    status.layers.map((layer) => ({
      label: layer.name,
      description: layerDescription(layer, status.write_layer === layer.name),
      detail: layer.repo,
      layer,
    })),
    {
      title,
      placeHolder: 'Select a layer',
    }
  );
  if (!picked) {
    return undefined;
  }
  if (!allowDisabled && !picked.layer.enabled) {
    vscode.window.showWarningMessage(`Layer ${picked.layer.name} is disabled. Enable it before selecting it.`);
    return undefined;
  }
  return picked.layer.name;
}

function layerDescription(
  layer: { kind?: string; mount?: string; enabled: boolean; branch?: string | null; status?: string },
  writeLayer = false
): string {
  return [
    layer.kind ?? 'git',
    layer.mount ?? '/',
    layer.enabled ? 'enabled' : 'disabled',
    layer.branch,
    layer.status,
    writeLayer ? 'write' : undefined,
  ]
    .filter(Boolean)
    .join(' ');
}

function layerProvidesFile(file: TreeFile, layerName: string): boolean {
  if (file.visibleLayer === layerName) {
    return true;
  }
  if (file.maskedByThisFile?.includes(layerName)) {
    return true;
  }
  return Boolean(file.maskedProviders?.some((provider) => provider.layer === layerName));
}

function hasModifiedFile(status: LayerGitStatus, filePath: string): boolean {
  return Boolean(status.modified_files?.some((item) => item.path === filePath));
}

function selectedFileNodes(node: FileNode | undefined, selected: FileNode[] | undefined): FileNode[] {
  const files = (selected?.length ? selected : node ? [node] : []).filter((item): item is FileNode => item instanceof FileNode);
  const byPath = new Map<string, FileNode>();
  for (const file of files) {
    byPath.set(file.file.path, file);
  }
  return [...byPath.values()];
}

function providerLines(
  title: string,
  providers: { layer?: string; sourcePath?: string; source_path?: string; mount?: string; revision?: string; commit?: string }[],
  filePath: string
): string[] {
  return [
    title,
    ...(providers.length
      ? providers.map((item) => {
          const source = item.sourcePath ?? item.source_path ?? filePath;
          const mount = item.mount ?? '/';
          const revision = item.revision ?? item.commit ?? 'unknown';
          return `  ${item.layer}: ${source} mounted at ${mount} @ ${revision}`;
        })
      : ['  none']),
  ];
}

async function explain(cli: LayerGitCli, workspace: vscode.Uri, filePath: string): Promise<void> {
  try {
    const result = await cli.explain(workspace, filePath);
    const visible = result.visible?.layer ?? 'none';
    const lines = [
      `Visible layer: ${visible}`,
      ...(result.selected_layer ? [`Selected layer: ${result.selected_layer}`] : []),
      `Source path: ${result.visible?.sourcePath ?? result.visible?.source_path ?? 'none'}`,
      `Mount: ${result.visible?.mount ?? 'none'}`,
      `Revision: ${result.visible?.revision ?? result.visible?.commit ?? 'none'}`,
      '',
      ...providerLines(result.hidden ? 'Hidden providers:' : 'Masked layers:', result.masked ?? [], filePath),
      ...(result.disabled_providers?.length
        ? ['', ...providerLines('Disabled providers:', result.disabled_providers, filePath)]
        : []),
      '',
      `Reason: ${result.reason ?? 'unknown'}`,
    ];
    cli.showDetails(`LayerGit Explain: ${filePath}`, lines);
    vscode.window.showInformationMessage(`Explained ${filePath} in the LayerGit output channel.`);
  } catch (error) {
    showError(error);
  }
}

async function runCliAction(
  cli: LayerGitCli,
  workspace: vscode.Uri,
  args: string[],
  refresh: () => void
): Promise<boolean> {
  try {
    await cli.run(workspace, args);
    refresh();
    return true;
  } catch (error) {
    showError(error);
    return false;
  }
}

async function runCliActionWithoutRefresh(
  cli: LayerGitCli,
  workspace: vscode.Uri,
  args: string[]
): Promise<boolean> {
  try {
    await cli.run(workspace, args);
    return true;
  } catch (error) {
    showError(error);
    return false;
  }
}

function showBatchResult(action: string, succeeded: number, total: number): void {
  if (succeeded === total) {
    vscode.window.showInformationMessage(`${action} for ${succeeded} file(s).`);
    return;
  }
  vscode.window.showWarningMessage(`${action} for ${succeeded} of ${total} file(s). See LayerGit output for failures.`);
}

async function requireLayerGitWorkspace(): Promise<vscode.Uri | undefined> {
  const workspace = await findLayerGitWorkspace();
  if (!workspace) {
    vscode.window.showWarningMessage('No LayerGit workspace found in this VS Code folder.');
  }
  return workspace;
}

async function requireWorkspaceFolder(): Promise<vscode.Uri | undefined> {
  const folder = vscode.workspace.workspaceFolders?.[0]?.uri;
  if (!folder) {
    vscode.window.showWarningMessage('Open a VS Code workspace folder first.');
  }
  return folder;
}

function showError(error: unknown): void {
  const message = error instanceof Error ? error.message : String(error);
  vscode.window.showErrorMessage(message);
}

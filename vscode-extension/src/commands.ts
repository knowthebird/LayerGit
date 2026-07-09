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
    vscode.commands.registerCommand('layergit.adoptFile', async (node?: FileNode, selected?: FileNode[]) => {
      await adoptFile(cli, node, selected, refresh);
    }),
    vscode.commands.registerCommand('layergit.deleteFile', async (node?: FileNode, selected?: FileNode[]) => {
      await deleteFile(cli, node, selected, refresh);
    }),
    vscode.commands.registerCommand('layergit.newFile', async () => {
      await createNewFile(cli, refresh);
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
    'Apply to Owning Layer',
    'Apply to Another Layer...',
    'Discard Edits and Switch'
  );
  if (!choice) {
    return false;
  }
  if (choice === 'Apply to Another Layer...') {
    return runAction(['apply', filePath, '--to', layerName]);
  }
  if (choice === 'Apply to Owning Layer') {
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
      `${filePath} is currently hidden by layer selection. Restore the inherited file before applying it to ${layerName}.`,
      { modal: true },
      'Restore Then Apply to Layer',
      'Keep Hidden'
    );
    if (choice === 'Restore Then Apply to Layer') {
      const restored = await runAction(['unuse', filePath]);
      if (restored) {
        return runAction(['apply', filePath, '--to', layerName]);
      }
    }
    return false;
  }
  const choice = await vscode.window.showWarningMessage(
    `${layerName} does not currently provide ${filePath}. Choose whether to hide the inherited file or copy the current buildtree file into ${layerName}.`,
    { modal: true },
    'Hide Inherited File',
    'Apply to Another Layer...'
  );
  if (choice === 'Hide Inherited File') {
    return runAction(['use', filePath, layerName, '--hide']);
  }
  if (choice === 'Apply to Another Layer...') {
    return runAction(['apply', filePath, '--to', layerName]);
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
        `Use the file context menu to hide this file or apply it to another layer explicitly.`,
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

async function adoptFile(
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
  const layerName = await pickLayerName(cli, workspace, files.length === 1 ? `Apply ${files[0].file.path} to Another Layer` : `Apply ${files.length} files to Another Layer`);
  if (!layerName) {
    return;
  }
  let succeeded = 0;
  for (const file of files) {
    const args = ['apply', file.file.path, '--to', layerName];
    if (layerProvidesFile(file.file, layerName)) {
      const confirmed = await vscode.window.showWarningMessage(
        `Layer ${layerName} already provides ${file.file.path}. Applying to another layer adopts the current buildtree file into that layer and will overwrite that layer's copy.`,
        { modal: true },
        'Overwrite Layer Copy'
      );
      if (confirmed !== 'Overwrite Layer Copy') {
        continue;
      }
      args.push('--force');
    }
    if (await runCliActionWithoutRefresh(cli, workspace, args)) {
      succeeded += 1;
    }
  }
  refresh();
  showBatchResult('Applied to another layer', succeeded, files.length);
}

async function deleteFile(
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
  let succeeded = 0;
  for (const file of files) {
    if (await deleteOneFile(cli, workspace, file, refresh)) {
      succeeded += 1;
    }
  }
  if (files.length > 1) {
    refresh();
    showBatchResult('Handled delete action', succeeded, files.length);
  }
}

async function deleteOneFile(
  cli: LayerGitCli,
  workspace: vscode.Uri,
  node: FileNode,
  refresh: () => void
): Promise<boolean> {
  const status = await cli.status(workspace);
  const filePath = node.file.path;
  if (hasModifiedFile(status, filePath)) {
    const handled = await handleDirtyFileBeforeDelete(cli, workspace, filePath, refresh);
    if (!handled) {
      return false;
    }
  }
  if (node.file.hidden) {
    const choice = await vscode.window.showWarningMessage(
      `${filePath} is hidden by layer selection. Source delete is not offered for hidden files.`,
      { modal: true },
      'Clear Selection',
      'Use Provider...',
      'Explain'
    );
    if (choice === 'Clear Selection') {
      return runCliAction(cli, workspace, ['unuse', filePath], refresh);
    }
    if (choice === 'Use Provider...') {
      await useLayerForFile(cli, node, undefined, refresh);
      return true;
    }
    if (choice === 'Explain') {
      await explain(cli, workspace, filePath);
      return true;
    }
    return false;
  }
  if (node.file.ownership === 'untracked' || !node.file.owned) {
    const choice = await vscode.window.showWarningMessage(
      `${filePath} is not owned by LayerGit. Deleting it will only remove the local buildtree file.`,
      { modal: true },
      'Delete Local Buildtree File',
      'Apply to Another Layer...'
    );
    if (choice === 'Delete Local Buildtree File') {
      await deleteBuildtreeFile(node.uri);
      refresh();
      return true;
    }
    if (choice === 'Apply to Another Layer...') {
      await adoptFile(cli, node, undefined, refresh);
      return true;
    }
    return false;
  }

  const choice = await vscode.window.showWarningMessage(
    `Delete ${filePath}?\n\nChoose what LayerGit should do.`,
    { modal: true },
    'Hide Inherited File',
    'Delete from Owning Layer...',
    'Delete Generated Copy Only'
  );
  if (choice === 'Hide Inherited File') {
    const layerName = await pickNonProviderLayerName(cli, workspace, node);
    if (!layerName) {
      return false;
    }
    return runCliAction(cli, workspace, ['use', filePath, layerName, '--hide'], refresh);
  }
  if (choice === 'Delete Generated Copy Only') {
    const confirmed = await vscode.window.showWarningMessage(
      `This only removes the generated buildtree copy of ${filePath}. The file may reappear after layer compose unless it is hidden or deleted from its source layer.`,
      { modal: true },
      'Delete Generated Copy'
    );
    if (confirmed !== 'Delete Generated Copy') {
      return false;
    }
    await deleteBuildtreeFile(node.uri);
    refresh();
    return true;
  }
  if (choice === 'Delete from Owning Layer...') {
    const owner = node.file.visibleLayer ?? 'unknown';
    const confirmed = await vscode.window.showWarningMessage(
      `This will delete ${filePath} from layer ${owner}.\n\nMasked providers will remain untouched.`,
      { modal: true },
      `Delete from ${owner}`
    );
    if (confirmed !== `Delete from ${owner}`) {
      return false;
    }
    await deleteBuildtreeFile(node.uri);
    return runCliAction(cli, workspace, ['apply', filePath, '--delete'], refresh);
  }
  return false;
}

async function handleDirtyFileBeforeDelete(
  cli: LayerGitCli,
  workspace: vscode.Uri,
  filePath: string,
  refresh: () => void
): Promise<boolean> {
  const choice = await vscode.window.showWarningMessage(
    `${filePath} has unapplied buildtree edits. What should LayerGit do before deleting or hiding it?`,
    { modal: true },
    'Apply to Owning Layer',
    'Apply to Another Layer...',
    'Discard/regenerate',
    'Cancel'
  );
  if (choice === 'Apply to Owning Layer') {
    return runCliAction(cli, workspace, ['apply', filePath], refresh);
  }
  if (choice === 'Apply to Another Layer...') {
    const layerName = await pickLayerName(cli, workspace, `Apply ${filePath} to Another Layer`);
    if (layerName) {
      await runCliAction(cli, workspace, ['apply', filePath, '--to', layerName], refresh);
    }
    return false;
  }
  if (choice === 'Discard/regenerate') {
    return runCliAction(cli, workspace, ['compose'], refresh);
  }
  return false;
}

async function createNewFile(cli: LayerGitCli, refresh: () => void): Promise<void> {
  const workspace = await requireLayerGitWorkspace();
  if (!workspace) {
    return;
  }
  const status = await cli.status(workspace);
  const requestedPath = await vscode.window.showInputBox({
    title: 'Create new LayerGit file',
    prompt: 'Path relative to buildtree/',
    validateInput: validateRelativeBuildtreePath,
  });
  if (!requestedPath) {
    return;
  }
  const filePath = requestedPath.trim().replace(/\\/g, '/').replace(/^\.\//, '');
  const layerName = await pickLayerName(cli, workspace, `Target layer for ${filePath}`);
  if (!layerName) {
    return;
  }
  const layer = status.layers.find((item) => item.name === layerName);
  const mountError = layer ? validatePathUnderLayerMount(filePath, layer.mount ?? '/') : undefined;
  if (mountError) {
    vscode.window.showErrorMessage(mountError);
    return;
  }
  const uri = joinWorkspacePath(workspace, status.output, filePath);
  if (await uriExists(uri)) {
    vscode.window.showWarningMessage(`${filePath} already exists in buildtree/.`);
    return;
  }
  await vscode.workspace.fs.createDirectory(parentUri(uri));
  await vscode.workspace.fs.writeFile(uri, new Uint8Array());
  const document = await vscode.workspace.openTextDocument(uri);
  await vscode.window.showTextDocument(document);

  const choice = await vscode.window.showInformationMessage(
    `Created ${filePath} in buildtree. Apply this file to layer ${layerName}? Applying to another layer adopts the current buildtree file into that layer.`,
    { modal: true },
    'Apply and stage',
    'Keep as unowned buildtree file'
  );
  if (choice === 'Apply and stage') {
    await runCliAction(cli, workspace, ['apply', filePath, '--to', layerName], refresh);
    return;
  }
  refresh();
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

async function pickNonProviderLayerName(
  cli: LayerGitCli,
  workspace: vscode.Uri,
  node: FileNode
): Promise<string | undefined> {
  const status = await cli.status(workspace);
  const candidates = status.layers.filter((layer) => layer.enabled && !layerProvidesFile(node.file, layer.name));
  if (!candidates.length) {
    vscode.window.showWarningMessage(`No enabled layer is available to hide ${node.file.path}.`);
    return undefined;
  }
  const picked = await vscode.window.showQuickPick(
    candidates.map((layer) => ({
      label: layer.name,
      description: layerDescription(layer, status.write_layer === layer.name),
      detail: layer.repo,
      layer,
    })),
    {
      title: `Hide ${node.file.path} by assigning a non-provider layer`,
      placeHolder: 'Select the layer to record the hide selection',
    }
  );
  return picked?.layer.name;
}

function validateRelativeBuildtreePath(value: string): string | undefined {
  const normalized = value.trim().replace(/\\/g, '/').replace(/^\.\//, '');
  if (!normalized) {
    return 'Path is required.';
  }
  if (normalized.startsWith('/') || normalized.split('/').includes('..')) {
    return 'Use a relative path inside buildtree/.';
  }
  if (normalized.endsWith('/')) {
    return 'Create files, not directories.';
  }
  return undefined;
}

function validatePathUnderLayerMount(filePath: string, mount: string): string | undefined {
  const normalizedMount = mount === '/' ? '' : mount.replace(/^\/+|\/+$/g, '');
  if (!normalizedMount) {
    return undefined;
  }
  if (filePath === normalizedMount || filePath.startsWith(`${normalizedMount}/`)) {
    return undefined;
  }
  return `Cannot create ${filePath} in layer mounted at /${normalizedMount}. Choose a path under /${normalizedMount} or select a different layer.`;
}

function joinWorkspacePath(workspace: vscode.Uri, ...relativeParts: string[]): vscode.Uri {
  const segments = relativeParts
    .flatMap((part) => part.replace(/^\.\//, '').split(/[\\/]+/))
    .filter(Boolean);
  return vscode.Uri.joinPath(workspace, ...segments);
}

function parentUri(uri: vscode.Uri): vscode.Uri {
  const parts = uri.path.split('/');
  return uri.with({ path: parts.slice(0, -1).join('/') || '/' });
}

async function uriExists(uri: vscode.Uri): Promise<boolean> {
  try {
    await vscode.workspace.fs.stat(uri);
    return true;
  } catch {
    return false;
  }
}

async function deleteBuildtreeFile(uri: vscode.Uri): Promise<void> {
  if (await uriExists(uri)) {
    await vscode.workspace.fs.delete(uri);
  }
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

import * as vscode from 'vscode';
import { LayerGitCli } from './cli';
import { DirectoryNode, FileNode } from './composedTreeView';
import { LayerItem } from './layersView';
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
      await runCliAction(cli, workspace, ['init'], refresh);
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
    vscode.commands.registerCommand('layergit.removeLayer', async (node?: LayerItem) => {
      await removeLayer(cli, node, refresh);
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
      await moveLayer(cli, node, 'moveup', refresh);
    }),
    vscode.commands.registerCommand('layergit.moveLayerDown', async (node?: LayerItem) => {
      await moveLayer(cli, node, 'movedown', refresh);
    }),
    vscode.commands.registerCommand('layergit.sendLayerToTop', async (node?: LayerItem) => {
      await moveLayer(cli, node, 'sendlayertotop', refresh);
    }),
    vscode.commands.registerCommand('layergit.sendLayerToBottom', async (node?: LayerItem) => {
      await moveLayer(cli, node, 'sendlayertobottom', refresh);
    }),
    vscode.commands.registerCommand('layergit.useLayerForFile', async (node?: FileNode) => {
      await useLayerForFile(cli, node, refresh);
    }),
    vscode.commands.registerCommand('layergit.useLayerForFolder', async (node?: DirectoryNode) => {
      await useLayerForFolder(cli, node, refresh);
    })
  );
}

async function addLayer(cli: LayerGitCli, refresh: () => void): Promise<void> {
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
    args.push('--name', name);
  }
  await runCliAction(cli, workspace, args, refresh);
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
  await runCliAction(cli, workspace, [command, node.layer.name], refresh);
}

async function useLayerForFile(
  cli: LayerGitCli,
  node: FileNode | undefined,
  refresh: () => void
): Promise<void> {
  const workspace = await requireLayerGitWorkspace();
  if (!workspace || !node) {
    return;
  }
  const status = await cli.status(workspace);
  const picked = await vscode.window.showQuickPick(
    status.layers.map((layer) => ({
      label: layer.name,
      description: `${layer.enabled ? 'enabled' : 'disabled'}${layer.branch ? ` ${layer.branch}` : ''}`,
      detail: layer.repo,
      layer,
    })),
    {
      title: `Use layer for ${node.file.path}`,
      placeHolder: 'Select the layer/repo to use for this file',
    }
  );
  if (!picked) {
    return;
  }
  if (!picked.layer.enabled) {
    vscode.window.showWarningMessage(`Layer ${picked.layer.name} is disabled. Enable it before selecting it for a file.`);
    return;
  }
  await runCliAction(cli, workspace, ['usefile', picked.layer.name, node.file.path], refresh);
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
    `Use layer ${layerName} for ${files.length} file(s) under ${node.relativePath}/? Files not provided by that layer may be hidden.`,
    { modal: true },
    'Apply Recursively'
  );
  if (confirmed !== 'Apply Recursively') {
    return;
  }

  let failures = 0;
  for (const file of files) {
    try {
      await cli.run(workspace, ['usefile', layerName, file.file.path]);
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
      description: `${layer.enabled ? 'enabled' : 'disabled'}${layer.branch ? ` ${layer.branch}` : ''}`,
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

function providerLines(
  title: string,
  providers: { layer?: string; sourcePath?: string; source_path?: string; revision?: string; commit?: string }[],
  filePath: string
): string[] {
  return [
    title,
    ...(providers.length
      ? providers.map((item) => {
          const source = item.sourcePath ?? item.source_path ?? filePath;
          const revision = item.revision ?? item.commit ?? 'unknown';
          return `  ${item.layer}: ${source} @ ${revision}`;
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
): Promise<void> {
  try {
    await cli.run(workspace, args);
    refresh();
  } catch (error) {
    showError(error);
  }
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

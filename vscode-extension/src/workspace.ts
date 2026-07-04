import * as path from 'path';
import * as vscode from 'vscode';

export async function findLayerGitWorkspace(): Promise<vscode.Uri | undefined> {
  const folders = vscode.workspace.workspaceFolders;
  if (!folders?.length) {
    return undefined;
  }

  for (const folder of folders) {
    const manifest = vscode.Uri.joinPath(folder.uri, 'layer.yaml');
    if (await exists(manifest)) {
      return folder.uri;
    }
  }

  const active = vscode.window.activeTextEditor?.document.uri;
  if (active?.scheme === 'file') {
    for (const folder of folders) {
      if (active.fsPath.startsWith(folder.uri.fsPath)) {
        const manifest = vscode.Uri.joinPath(folder.uri, 'layer.yaml');
        if (await exists(manifest)) {
          return folder.uri;
        }
      }
    }
  }

  return undefined;
}

export async function exists(uri: vscode.Uri): Promise<boolean> {
  try {
    await vscode.workspace.fs.stat(uri);
    return true;
  } catch {
    return false;
  }
}

export function relativeComposedPath(workspace: vscode.Uri, file: vscode.Uri, output = 'buildtree'): string | undefined {
  const outputRoot = path.join(workspace.fsPath, output);
  if (!file.fsPath.startsWith(outputRoot)) {
    return undefined;
  }
  return path.relative(outputRoot, file.fsPath).replaceAll(path.sep, '/');
}

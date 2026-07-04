import { execFile } from 'child_process';
import { existsSync } from 'fs';
import * as path from 'path';
import * as vscode from 'vscode';
import { ExplainResult, LayerGitStatus, LayerGitTree } from './models';

export class LayerGitCli {
  constructor(
    private readonly output: vscode.OutputChannel,
    private readonly extensionRoot?: vscode.Uri
  ) {}

  showDetails(title: string, lines: string[]): void {
    this.output.appendLine('');
    this.output.appendLine(title);
    this.output.appendLine('='.repeat(title.length));
    for (const line of lines) {
      this.output.appendLine(line);
    }
    this.output.show(true);
  }

  async status(workspace: vscode.Uri): Promise<LayerGitStatus> {
    return this.runJson<LayerGitStatus>(workspace, ['status', '--json']);
  }

  async tree(workspace: vscode.Uri): Promise<LayerGitTree> {
    return this.runJson<LayerGitTree>(workspace, ['tree', '--json']);
  }

  async explain(workspace: vscode.Uri, filePath: string): Promise<ExplainResult> {
    return this.runJson<ExplainResult>(workspace, ['explain', filePath, '--json']);
  }

  async run(workspace: vscode.Uri, args: string[]): Promise<string> {
    const command = vscode.workspace.getConfiguration('layergit').get<string>('command', 'layer');
    const parts = resolveCommand(command, workspace, this.extensionRoot);
    const executable = parts[0];
    const finalArgs = [...parts.slice(1), ...args];
    this.output.appendLine(`$ ${[executable, ...finalArgs].join(' ')}`);

    return new Promise((resolve, reject) => {
      execFile(
        executable,
        finalArgs,
        { cwd: workspace.fsPath, windowsHide: true },
        (error, stdout, stderr) => {
          if (stdout) {
            this.output.append(stdout);
          }
          if (stderr) {
            this.output.append(stderr);
          }
          if (error) {
            const enoent = (error as NodeJS.ErrnoException).code === 'ENOENT';
            const message = enoent
              ? `LayerGit CLI not found: ${executable}. Install the package or set layergit.command to the venv Python command.`
              : stderr.trim() || error.message;
            reject(new Error(message));
            return;
          }
          resolve(stdout);
        }
      );
    });
  }

  private async runJson<T>(workspace: vscode.Uri, args: string[]): Promise<T> {
    const stdout = await this.run(workspace, args);
    try {
      return JSON.parse(stdout) as T;
    } catch (error) {
      throw new Error(`LayerGit returned invalid JSON for command: ${args.join(' ')}`);
    }
  }
}

function splitCommand(command: string): string[] {
  const matches = command.match(/(?:[^\s"]+|"[^"]*")+/g) ?? ['layer'];
  return matches.map((part) => part.replace(/^"|"$/g, ''));
}

function resolveCommand(command: string, workspace: vscode.Uri, extensionRoot?: vscode.Uri): string[] {
  if (command.trim() !== 'layer') {
    return splitCommand(command);
  }

  for (const root of candidateVenvRoots(workspace, extensionRoot)) {
    const posixPython = path.join(root, '.venv', 'bin', 'python');
    if (existsSync(posixPython)) {
      return [posixPython, '-m', 'layergit.cli'];
    }

    const windowsPython = path.join(root, '.venv', 'Scripts', 'python.exe');
    if (existsSync(windowsPython)) {
      return [windowsPython, '-m', 'layergit.cli'];
    }
  }

  return ['layer'];
}

function candidateVenvRoots(workspace: vscode.Uri, extensionRoot?: vscode.Uri): string[] {
  const roots = [workspace.fsPath];
  if (extensionRoot) {
    roots.push(extensionRoot.fsPath);
    roots.push(path.dirname(extensionRoot.fsPath));
  }
  return [...new Set(roots)];
}

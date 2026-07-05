export interface LayerGitStatus {
  workspace: string;
  output: string;
  write_layer?: string;
  layers: LayerInfo[];
  conflicts: unknown[];
  warnings: unknown[];
}

export interface LayerInfo {
  index: number;
  name: string;
  kind?: 'git' | 'local';
  repo?: string;
  enabled: boolean;
  position?: 'top' | 'bottom' | null;
  branch?: string | null;
  revision?: string | null;
  commit?: string | null;
  dirty: boolean;
  status: string;
  top?: boolean;
}

export interface LayerGitTree {
  workspace: string;
  output: string;
  files: TreeFile[];
}

export interface TreeFile {
  path: string;
  type: 'file';
  owned?: boolean;
  ownership?: 'composed' | 'stale' | 'untracked';
  visibleLayer?: string;
  visibleLayerIndex?: number;
  selectedLayer?: string;
  hidden?: boolean;
  maskedByThisFile: string[];
}

export interface ExplainResult {
  path: string;
  visible?: ExplainProvider | null;
  masked: ExplainProvider[];
  disabled_providers?: ExplainProvider[];
  selected_layer?: string;
  hidden?: boolean;
  reason?: string;
}

export interface ExplainProvider {
  layer: string;
  layerIndex?: number;
  source_path?: string;
  sourcePath?: string;
  commit?: string;
  revision?: string;
}

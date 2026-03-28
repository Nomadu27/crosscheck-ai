/**
 * crosscheck-ai VS Code Extension
 * Phase 2 — Inline observer + "Fix with Crosscheck" button
 *
 * Architecture:
 *   - Spawns crosscheck CLI as a child process (Python must be installed)
 *   - Streams JSON events from stdout
 *   - Renders inline diagnostics via the VS Code Diagnostics API
 *   - Shows a CodeLens "Fix with Crosscheck" above flagged functions
 *   - Status bar item shows live score / verdict
 */

import * as vscode from "vscode";
import { spawn, ChildProcess } from "child_process";
import * as path from "path";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface CrosscheckIssue {
  severity: "critical" | "major" | "minor";
  location: string;
  description: string;
  fix: string;
}

interface CrosscheckReport {
  verdict: "APPROVED" | "REVISE" | "MAX_ROUNDS_REACHED";
  final_score: number;
  issues: CrosscheckIssue[];
  summary: string;
  final_content?: string;
}

// ---------------------------------------------------------------------------
// Extension activation
// ---------------------------------------------------------------------------

let diagnosticCollection: vscode.DiagnosticCollection;
let statusBarItem: vscode.StatusBarItem;
let observerTimer: NodeJS.Timeout | undefined;
let lastReport: CrosscheckReport | undefined;
let fixCodeLensProvider: FixCodeLensProvider | undefined;

export function activate(context: vscode.ExtensionContext) {
  diagnosticCollection = vscode.languages.createDiagnosticCollection("crosscheck");
  context.subscriptions.push(diagnosticCollection);

  statusBarItem = vscode.window.createStatusBarItem(
    vscode.StatusBarAlignment.Right,
    100
  );
  statusBarItem.command = "crosscheck.showReport";
  statusBarItem.text = "$(eye) crosscheck";
  statusBarItem.tooltip = "crosscheck-ai — click to show last report";
  statusBarItem.show();
  context.subscriptions.push(statusBarItem);

  // Register CodeLens provider for "Fix with Crosscheck" button
  fixCodeLensProvider = new FixCodeLensProvider();
  context.subscriptions.push(
    vscode.languages.registerCodeLensProvider(
      { scheme: "file" },
      fixCodeLensProvider
    )
  );

  // Commands
  context.subscriptions.push(
    vscode.commands.registerCommand("crosscheck.reviewFile", reviewCurrentFile),
    vscode.commands.registerCommand("crosscheck.reviewSelection", reviewSelection),
    vscode.commands.registerCommand("crosscheck.fixWithCrosscheck", fixWithCrosscheck),
    vscode.commands.registerCommand("crosscheck.toggleObserver", toggleObserver),
    vscode.commands.registerCommand("crosscheck.showReport", showReport)
  );

  // Observer: watch on save
  context.subscriptions.push(
    vscode.workspace.onDidSaveTextDocument((doc) => {
      const cfg = vscode.workspace.getConfiguration("crosscheck");
      if (!cfg.get<boolean>("observerEnabled")) return;
      scheduleObserverReview(doc);
    })
  );

  console.log("crosscheck-ai extension activated");
}

export function deactivate() {
  if (observerTimer) clearTimeout(observerTimer);
  diagnosticCollection?.dispose();
  statusBarItem?.dispose();
}

// ---------------------------------------------------------------------------
// Review commands
// ---------------------------------------------------------------------------

async function reviewCurrentFile() {
  const editor = vscode.window.activeTextEditor;
  if (!editor) return;
  await runCrosscheck(editor.document.getText(), editor.document.uri);
}

async function reviewSelection() {
  const editor = vscode.window.activeTextEditor;
  if (!editor) return;
  const sel = editor.selection;
  if (sel.isEmpty) {
    vscode.window.showWarningMessage("crosscheck: No selection. Select some code first.");
    return;
  }
  const text = editor.document.getText(sel);
  await runCrosscheck(text, editor.document.uri);
}

async function fixWithCrosscheck() {
  const editor = vscode.window.activeTextEditor;
  if (!editor) return;

  if (!lastReport?.final_content) {
    vscode.window.showInformationMessage(
      "crosscheck: Run a review first to get a revised version."
    );
    return;
  }

  const apply = await vscode.window.showInformationMessage(
    `crosscheck: Apply the revised code? (Score: ${lastReport.final_score?.toFixed(1)}/10)`,
    "Apply",
    "Cancel"
  );
  if (apply !== "Apply") return;

  const edit = new vscode.WorkspaceEdit();
  const fullRange = new vscode.Range(
    editor.document.positionAt(0),
    editor.document.positionAt(editor.document.getText().length)
  );
  edit.replace(editor.document.uri, fullRange, lastReport.final_content);
  await vscode.workspace.applyEdit(edit);
  vscode.window.showInformationMessage("✅ crosscheck: Revised code applied.");
}

function toggleObserver() {
  const cfg = vscode.workspace.getConfiguration("crosscheck");
  const current = cfg.get<boolean>("observerEnabled") ?? false;
  cfg.update("observerEnabled", !current, vscode.ConfigurationTarget.Workspace);
  const msg = !current ? "✅ crosscheck observer ON" : "🔇 crosscheck observer OFF";
  statusBarItem.text = !current ? "$(eye) crosscheck 👁" : "$(eye) crosscheck";
  vscode.window.showInformationMessage(msg);
}

function showReport() {
  if (!lastReport) {
    vscode.window.showInformationMessage("crosscheck: No report yet. Run a review first.");
    return;
  }
  const panel = vscode.window.createWebviewPanel(
    "crosscheck.report",
    "crosscheck Report",
    vscode.ViewColumn.Beside,
    { enableScripts: false }
  );
  panel.webview.html = renderReportHTML(lastReport);
}

// ---------------------------------------------------------------------------
// Observer: debounced save handler
// ---------------------------------------------------------------------------

function scheduleObserverReview(doc: vscode.TextDocument) {
  const supportedLangs = new Set([
    "python", "typescript", "javascript", "typescriptreact",
    "javascriptreact", "go", "rust", "kotlin", "java",
  ]);
  if (!supportedLangs.has(doc.languageId)) return;

  if (observerTimer) clearTimeout(observerTimer);
  const cfg     = vscode.workspace.getConfiguration("crosscheck");
  const debounce = cfg.get<number>("observerDebounce") ?? 1500;

  observerTimer = setTimeout(async () => {
    statusBarItem.text = "$(sync~spin) crosscheck…";
    await runCrosscheck(doc.getText(), doc.uri, true);
  }, debounce);
}

// ---------------------------------------------------------------------------
// Core: spawn crosscheck CLI and stream results
// ---------------------------------------------------------------------------

async function runCrosscheck(
  content:     string,
  uri:         vscode.Uri,
  observerMode = false
): Promise<void> {
  const cfg     = vscode.workspace.getConfiguration("crosscheck");
  const apiKey  = cfg.get<string>("apiKey") || process.env.CROSSCHECK_API_KEY || "";
  const mode    = cfg.get<string>("mode") || "balanced";

  if (!apiKey) {
    vscode.window.showErrorMessage(
      'crosscheck: No API key. Set "crosscheck.apiKey" in settings or CROSSCHECK_API_KEY env var.'
    );
    return;
  }

  statusBarItem.text = "$(sync~spin) crosscheck reviewing…";
  diagnosticCollection.delete(uri);

  // Build CLI command
  const args = [
    "review", "--stdin",
    "--output", "json",
    "--api-key", apiKey,
    "--mode", mode,
    "--max-rounds", observerMode ? "1" : "3",
  ];

  let output = "";
  let proc: ChildProcess;

  try {
    proc = spawn("crosscheck", args, { shell: true });
    proc.stdin?.write(content);
    proc.stdin?.end();

    proc.stdout?.on("data", (chunk: Buffer) => {
      output += chunk.toString();
    });

    await new Promise<void>((resolve, reject) => {
      proc.on("close", (code) => {
        if (code !== null && code > 1) {
          reject(new Error(`crosscheck exited with code ${code}`));
        } else {
          resolve();
        }
      });
      proc.on("error", reject);
    });
  } catch (err) {
    statusBarItem.text = "$(error) crosscheck error";
    vscode.window.showErrorMessage(`crosscheck failed: ${err}`);
    return;
  }

  // Parse JSON result
  try {
    const jsonStart = output.indexOf("{");
    if (jsonStart === -1) throw new Error("No JSON in output");
    const report: CrosscheckReport = JSON.parse(output.slice(jsonStart));
    lastReport = report;
    applyDiagnostics(uri, report);
    updateStatusBar(report);
    fixCodeLensProvider?.refresh();

    if (!observerMode && report.verdict !== "APPROVED") {
      vscode.window.showWarningMessage(
        `crosscheck: ${report.verdict} — Score ${report.final_score.toFixed(1)}/10. Click status bar for details.`
      );
    }
  } catch {
    statusBarItem.text = "$(warning) crosscheck parse error";
  }
}

// ---------------------------------------------------------------------------
// Diagnostics
// ---------------------------------------------------------------------------

function applyDiagnostics(uri: vscode.Uri, report: CrosscheckReport) {
  const diagnostics: vscode.Diagnostic[] = [];
  const doc = vscode.workspace.textDocuments.find((d) => d.uri.toString() === uri.toString());

  for (const issue of report.issues || []) {
    const line  = extractLineNumber(issue.location, doc?.lineCount ?? 1) - 1;
    const range = new vscode.Range(line, 0, line, 999);

    const severity =
      issue.severity === "critical" ? vscode.DiagnosticSeverity.Error
      : issue.severity === "major"  ? vscode.DiagnosticSeverity.Warning
      :                               vscode.DiagnosticSeverity.Information;

    const d = new vscode.Diagnostic(range, `[crosscheck] ${issue.description}`, severity);
    d.source = "crosscheck-ai";
    if (issue.fix) {
      d.relatedInformation = [
        new vscode.DiagnosticRelatedInformation(
          new vscode.Location(uri, range),
          `Fix: ${issue.fix}`
        ),
      ];
    }
    diagnostics.push(d);
  }

  diagnosticCollection.set(uri, diagnostics);
}

function extractLineNumber(location: string, maxLines: number): number {
  const m = location.match(/line[s]?\s*(\d+)/i);
  if (m) return Math.min(parseInt(m[1]), maxLines);
  return 1;
}

// ---------------------------------------------------------------------------
// Status bar
// ---------------------------------------------------------------------------

function updateStatusBar(report: CrosscheckReport) {
  const icon   = report.verdict === "APPROVED" ? "$(check)" : "$(warning)";
  const color  = report.verdict === "APPROVED" ? new vscode.ThemeColor("statusBar.foreground")
                                                : new vscode.ThemeColor("statusBarItem.warningBackground");
  statusBarItem.text            = `${icon} crosscheck ${report.final_score?.toFixed(1) ?? "?"}/10`;
  statusBarItem.backgroundColor = color;
}

// ---------------------------------------------------------------------------
// CodeLens: "Fix with Crosscheck" button
// ---------------------------------------------------------------------------

class FixCodeLensProvider implements vscode.CodeLensProvider {
  private _onDidChangeCodeLenses = new vscode.EventEmitter<void>();
  readonly onDidChangeCodeLenses = this._onDidChangeCodeLenses.event;

  refresh() { this._onDidChangeCodeLenses.fire(); }

  provideCodeLenses(doc: vscode.TextDocument): vscode.CodeLens[] {
    if (!lastReport?.final_content) return [];
    if (lastReport.verdict === "APPROVED") return [];

    // Show "Fix with Crosscheck" at the top of the file
    const range = new vscode.Range(0, 0, 0, 0);
    return [
      new vscode.CodeLens(range, {
        title:   `🔧 Fix with Crosscheck (score: ${lastReport.final_score?.toFixed(1)}/10)`,
        command: "crosscheck.fixWithCrosscheck",
      }),
    ];
  }
}

// ---------------------------------------------------------------------------
// Report webview HTML
// ---------------------------------------------------------------------------

function renderReportHTML(report: CrosscheckReport): string {
  const verdictColor = report.verdict === "APPROVED" ? "#4caf50" : "#f44336";
  const issues = (report.issues ?? [])
    .map(
      (i) => `
      <div class="issue ${i.severity}">
        <strong>[${i.severity.toUpperCase()}]</strong> ${i.description}
        ${i.fix ? `<div class="fix">→ Fix: ${i.fix}</div>` : ""}
      </div>`
    )
    .join("");

  return `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <style>
    body { font-family: var(--vscode-font-family); padding: 16px; color: var(--vscode-foreground); }
    h1 { color: ${verdictColor}; }
    .issue { margin: 8px 0; padding: 8px; border-radius: 4px; border-left: 4px solid #888; }
    .issue.critical { border-color: #f44336; background: rgba(244,67,54,0.1); }
    .issue.major    { border-color: #ff9800; background: rgba(255,152,0,0.1); }
    .issue.minor    { border-color: #2196f3; background: rgba(33,150,243,0.1); }
    .fix { color: #888; margin-top: 4px; font-size: 0.9em; }
    .score { font-size: 2em; font-weight: bold; color: ${verdictColor}; }
  </style>
</head>
<body>
  <h1>${report.verdict}</h1>
  <div class="score">${report.final_score?.toFixed(1) ?? "?"}/10</div>
  <p>${report.summary ?? ""}</p>
  <h2>Issues</h2>
  ${issues || "<p>No issues found.</p>"}
</body>
</html>`;
}

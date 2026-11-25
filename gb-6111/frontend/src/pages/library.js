import { libraryApi } from "../api/library.js";
import { api } from "../api/workbench.js";
import { errorText, field } from "../components/common/fields.js";
import { empty, pageHeading, section } from "../components/common/panel.js";
import { table } from "../components/common/table.js";
import { submit } from "../hooks/submit.js";
import { el } from "../utils/dom.js";
import { cell, time } from "../utils/format.js";

const LABELS = {organs: "琴体", visits: "维修工单", readings: "测记"};

function summaryText(counts) {
  return ["organs", "visits", "readings"].map(key => `${counts[key]} ${LABELS[key]}`).join(" · ");
}

function conflictTable(conflicts) {
  const rows = conflicts.map(conflict => {
    const label = conflict.relation === "volume" ? conflict.label : `${conflict.label} ${conflict.record_id}`;
    return [label,
      Object.entries(conflict.existing).map(([key, value]) => `${key}=${cell(value)}`).join("，"),
      Object.entries(conflict.incoming).map(([key, value]) => `${key}=${cell(value)}`).join("，")];
  });
  return table(["冲突记录", "在库内容", "本卷内容"], rows, "同号冲突记录");
}

function admitForm(config, runs, reload) {
  const runOptions = el("select", {name: "run_id"},
    el("option", {value: ""}, "不使用执行批次（手工录入 JSON）"),
    runs.map(run => el("option", {value: run.id}, `#${run.id} ${run.case_title}`)));
  const feedback = el("div", {class: "feedback-slot"});
  const form = el("form", {id: "library-admit-form"},
    field("卷号", el("input", {name: "code", maxlength: 48, placeholder: "留空则用批次资料编号"})),
    field("名称", el("input", {name: "title", maxlength: 120, placeholder: "留空则用批次名称"})),
    field("核验批次", runOptions, "选择通过核验的复原批次，按选中闭包入库。"),
    field("手工记录 JSON", el("textarea", {name: "records", rows: 8, spellcheck: "false"},
      '{"organs":[],"visits":[],"readings":[]}')),
    errorText(), el("button", {type: "submit"}, "归入维修库"), feedback);
  submit(form, async values => {
    const payload = {code: values.get("code").trim() || null, title: values.get("title").trim() || null};
    const runId = values.get("run_id");
    if (runId) payload.run_id = Number(runId);
    else {
      let records;
      try {
        records = JSON.parse(values.get("records"));
      } catch {
        throw new Error("记录 JSON 无法解析，请检查括号、引号和逗号");
      }
      Object.assign(payload, {organs: records.organs, visits: records.visits, readings: records.readings});
    }
    const outcome = await libraryApi.admit(payload);
    feedback.replaceChildren(renderOutcome(outcome, reload));
    reload();
  });
  return form;
}

function renderOutcome(outcome, reload) {
  if (outcome.status === "rejected") {
    return el("div", {"data-testid": "library-conflict", class: "conflict"},
      el("p", {role: "alert"}, "同号记录内容不一致，整卷未入库。请先核对下列冲突记录。"),
      conflictTable(outcome.conflicts));
  }
  if (outcome.status === "unchanged") {
    return el("p", {class: "ok", "data-testid": "library-unchanged"},
      `卷 ${outcome.volume.code} 已在库且内容一致，未生成重复记录。`);
  }
  return el("p", {class: "ok", "data-testid": "library-admitted"},
    `已归入 ${outcome.volume.code}：新建 ${summaryText(outcome.created)}，共用 ${summaryText(outcome.shared)}。`);
}

function volumeTable(volumes, withdraw) {
  const rows = volumes.map(volume => [volume.code, volume.title, summaryText(volume.counts),
    volume.admitted_by, time(volume.admitted_at), volume.source_run_id ? `#${volume.source_run_id}` : "手工",
    withdraw(volume)]);
  return table(["卷号", "名称", "记录", "入库人", "入库时间", "批次", "操作"], rows, "在库维护卷");
}

function recordTables(volume) {
  const readings = volume.records.readings || [];
  const visits = volume.records.visits || [];
  const organs = volume.records.organs || [];
  return [
    section(`${LABELS.organs}（${organs.length}）`,
      organs.length ? table(["编号", "名称"], organs.map(item => [item.organ_id, item.label]), "琴体") : empty("无。")),
    section(`${LABELS.visits}（${visits.length}）`,
      visits.length ? table(["编号", "琴体", "维修日期"],
        visits.map(item => [item.visit_id, item.organ_id, item.service_day]), "维修工单") : empty("无。")),
    section(`${LABELS.readings}（${readings.length}）`,
      readings.length ? table(["编号", "工单", "内容"],
        readings.map(item => [item.reading_id, item.visit_id, item.detail]), "测记") : empty("无。")),
  ];
}

export async function libraryPage(config, user, signal) {
  const [catalogue, runs] = await Promise.all([
    libraryApi.catalogue(signal),
    api.runs(),
  ]);
  if (signal.aborted) return el("div");
  const operator = user.role === "operator";
  const verified = runs.filter(item => item.status === "done" && item.result?.metrics?.["还原工单"]);
  const detailHost = el("div", {class: "stack"});
  const issue = errorText();

  const withdrawButton = volume => el("button", {type: "button", class: "quiet", onclick: async event => {
    if (!window.confirm(`撤回卷 ${volume.code}？其它在库卷仍在引用的记录会保留。`)) return;
    event.target.disabled = true;
    issue.textContent = "";
    try {
      const outcome = await libraryApi.withdraw(volume.id);
      issue.textContent = `已撤回 ${outcome.volume.code}：清走 ${summaryText(outcome.removed)}，` +
        `保留共用 ${summaryText(outcome.retained)}。`;
      await showCatalogue();
    } catch (error) {
      issue.textContent = error.message;
      event.target.disabled = false;
    }
  }}, "撤回");

  const catalogueHost = el("div", {class: "stack"});
  const picker = el("select", {"aria-label": "查看在库卷", onchange: event => {
    if (event.target.value) showVolume(Number(event.target.value));
  }});
  async function showCatalogue() {
    const next = await libraryApi.catalogue();
    catalogueHost.replaceChildren(
      next.volumes.length ? volumeTable(next.volumes, operator ? withdrawButton : () => "—")
        : empty("维修库还没有在库卷。"));
    const previous = picker.value;
    picker.replaceChildren(el("option", {value: ""}, "选择一卷查看记录"),
      ...next.volumes.map(volume => el("option", {value: volume.id}, `${volume.code} · ${volume.title}`)));
    if (next.volumes.some(volume => String(volume.id) === previous)) picker.value = previous;
    if (!next.volumes.length) detailHost.replaceChildren();
  }
  catalogueHost.append(catalogue.volumes.length
    ? volumeTable(catalogue.volumes, operator ? withdrawButton : () => "—")
    : empty("维修库还没有在库卷。"));
  picker.append(el("option", {value: ""}, "选择一卷查看记录"),
    ...catalogue.volumes.map(volume => el("option", {value: volume.id}, `${volume.code} · ${volume.title}`)));

  async function showVolume(volumeId) {
    const volume = await libraryApi.volume(volumeId);
    detailHost.replaceChildren(
      el("p", {}, `卷号 ${volume.code} · SHA-256 ${volume.digest}`),
      ...recordTables(volume));
  }

  return el("div", {class: "page library"},
    pageHeading(config.library_module, `${summaryText(catalogue.counts)} · 同号共用，冲突整卷不入库`),
    issue,
    section("在库维护卷", catalogueHost),
    section("查看整卷记录", picker, detailHost),
    operator ? section("归入维护卷", admitForm(config, verified, showCatalogue)) : null);
}

import fs from 'node:fs/promises';
import {FileBlob, SpreadsheetFile} from '@oai/artifact-tool';

const outputDir = 'D:/WorkSpace/Codex_WorkSpace/LB_Penguin/outputs/chart-mapping-20261015';
const data = JSON.parse(await fs.readFile(`${outputDir}/bm25-matches.json`, 'utf8'));
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(data.source_workbook));
console.log((await workbook.inspect({kind:'sheet', include:'id,name', maxChars:1500})).ndjson);
const original = await workbook.render({sheetName:'14',range:'A1:H8',scale:1,format:'png'});
await fs.writeFile(`${outputDir}/before.png`,new Uint8Array(await original.arrayBuffer()));
const headers = ['資料庫歌曲ID','資料庫譜面ID','資料庫難度','對應狀態','BM25分數','候選歌曲','來源列編號'];
const changed = new Set();
for (const row of data.rows) {
  const candidate = row.selected;
  for (const loc of row.locations) {
    const sheet = workbook.worksheets.getItem(loc.sheet);
    changed.add(loc.sheet);
    sheet.getRange(`L${loc.excel_row}:R${loc.excel_row}`).values = [[
      candidate?.song_id ?? null,candidate?.chart_id ?? null,row.difficulty,
      row.status,candidate?.bm25 ?? row.candidates[0]?.bm25 ?? null,
      (candidate ?? row.candidates[0])?.title ?? '',row.source_row
    ]];
  }
}
for(const name of changed) {
  const sheet=workbook.worksheets.getItem(name);
  sheet.getRange('L1:R1').values=[headers];
  sheet.getRange('L1:R1').format={fill:'#24445C',font:{bold:true,color:'#FFFFFF'},rowHeight:30,wrapText:true};
  sheet.getRange('L:R').format.columnWidth=19;
  sheet.getRange('O:O').format.columnWidth=38;
  sheet.getRange('Q:Q').format.columnWidth=65;
  sheet.getRange('P2:P1500').setNumberFormat('0.000');
}
workbook.recalculate();
console.log((await workbook.inspect({kind:'table',range:"'14'!L1:R7",tableMaxRows:7,tableMaxCols:7,maxChars:2000})).ndjson);
console.log((await workbook.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#NUM!',options:{useRegex:true,maxResults:10},maxChars:1000})).ndjson);
const preview=await workbook.render({sheetName:'14',range:'L1:R8',scale:1,format:'png'});
await fs.writeFile(`${outputDir}/ids-preview.png`,new Uint8Array(await preview.arrayBuffer()));
await (await SpreadsheetFile.exportXlsx(workbook)).save(`${outputDir}/ChunithmChart_with_ID.xlsx`);
console.log('workbook saved');

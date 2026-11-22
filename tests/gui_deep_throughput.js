'use strict';
// The deep permuter's throughput line (deep-progress.js), which the project's Deep permuter activity shows.
const fs=require('node:fs'), vm=require('node:vm'), assert=require('node:assert/strict');
const ctx=vm.createContext({});
vm.runInContext(fs.readFileSync('src/decompnet/app/static/js/deep-progress.js','utf8'),ctx);
const text=value=>ctx.deepThroughputText(value);
let line=text({active_compiles:8,compile_slots:10,candidates_per_second:123.45,compiles_per_second:98.76,compiler_queue_seconds:1.2,compiler_setup_seconds:.3,app_cpu_percent:150});
assert.match(line,/8\/10 compiles active/);
assert.match(line,/123.5 candidates\/s/);
assert.match(line,/98.8 compiles\/s/);
assert.match(line,/compiler and generator processes excluded/);
assert.equal(text(undefined),'');
const html=fs.readFileSync('src/decompnet/app/static/index.html','utf8');
for(const id of ['deepSweepMaximum','deepSweepCompileSlots'])assert.ok(html.includes(`id="${id}"`));
line=text({local_proposal_generation_seconds:2.5,cleanup_generation_seconds:.1,compiler_execute_seconds:8.2,link_seconds:.4});
assert.match(line,/2.5s local proposals/);
assert.match(line,/0.1s cleanup/);
assert.match(line,/8.2s compiler work \(summed workers\)/);
assert.match(line,/0.4s linking/);

line=text({compiler_slot_utilization_percent:62.5,history_write_seconds:1.2,history_transactions:3});
assert.match(line,/62.5% average slots busy/);
assert.match(line,/1.2s history writes \(3 transactions\)/);

line=text({active_compiles:2,compile_slots:16,compiler_waiting_search:1,compiler_waiting_background:0,
  compiler_waiting_shared:3,shared_active_compiles:16,shared_compile_slots:16});
assert.match(line,/2\/16 compiles active/);
assert.match(line,/waiting now 4 \(search 1, background 0, shared 3\)/);
assert.match(line,/16\/16 shared compiler slots busy/);
assert.doesNotMatch(text({compiler_waiting_search:0,compiler_waiting_background:0,compiler_waiting_shared:0}),/waiting now/);
assert.match(text({rank_literals_cpu_seconds:.006,rank_batches:4}),/literal extraction 0.006 CPU s across 4 ranked batches \(1.50 ms\/batch\)/);
assert.doesNotMatch(text({rank_batches:0}),/literal extraction/);

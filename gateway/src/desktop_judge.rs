//! Private stdio transport for the desktop attempt worker.

use std::io::{self, BufRead, Write};

use serde_json::{json, Value};

use crate::{mips_batch, mips_diff, mips_link, mips_rom, mips_uso, rom_project};

struct Reader<'a>(rom_project::View<'a>);

impl mips_rom::Reader for Reader<'_> {
    async fn ask(&mut self, request: Value) -> Result<Value, String> {
        self.0
            .lookup(&request)?
            .ok_or_else(|| "unsupported desktop ROM lookup".into())
    }
}

struct StdioReader<'a, I, O> {
    input: &'a mut I,
    output: &'a mut O,
}

impl<I: BufRead, O: Write> mips_rom::Reader for StdioReader<'_, I, O> {
    async fn ask(&mut self, request: Value) -> Result<Value, String> {
        writeln!(self.output, "{}", json!({"rom_lookup": request}))
            .and_then(|()| self.output.flush())
            .map_err(|error| error.to_string())?;
        let mut line = String::new();
        self.input
            .read_line(&mut line)
            .map_err(|error| error.to_string())?;
        let reply: Value = serde_json::from_str(&line).map_err(|error| error.to_string())?;
        reply
            .get("rom_value")
            .cloned()
            .ok_or_else(|| "desktop ROM lookup failed".into())
    }
}

#[derive(Default)]
struct Judge {
    project: Option<(String, rom_project::Project)>,
}

impl Judge {
    fn project_view(&mut self, request: &mut Value) -> Result<Reader<'_>, String> {
        if let Some(inputs) = request
            .as_object_mut()
            .and_then(|req| req.remove("project_inputs"))
        {
            let key = request["project_key"]
                .as_str()
                .ok_or("missing project key")?;
            self.project = Some((key.to_owned(), rom_project::Project::load(inputs)?));
        }
        let (key, project) = self.project.as_ref().ok_or("missing desktop ROM project")?;
        if request["project_key"].as_str() != Some(key.as_str()) {
            return Err("desktop ROM project changed".into());
        }
        Ok(Reader(project.view(request["rom_offset"].as_i64())?))
    }

    async fn judge_batch(&mut self, request: &mut Value) -> Result<Value, String> {
        let mut reader = if request["overlay"].is_null() {
            Some(self.project_view(request)?)
        } else {
            None
        };
        let objects = request["objects"]
            .as_array()
            .filter(|items| items.len() <= 8)
            .ok_or("invalid compiler batch")?;
        let Some(comparison) = mips_diff::PreparedComparison::from_value(&request["comparison"])?
        else {
            return Ok(Value::Null);
        };
        let align = request["align_branches"].as_bool().unwrap_or(false);
        let mut results = Vec::with_capacity(objects.len());
        for object in objects {
            let linked = if let Some(reader) = reader.as_mut() {
                mips_rom::link(
                    &json!({"object": object, "target": request["target"]}),
                    reader,
                )
                .await
            } else {
                mips_uso::link(&json!({"object": object, "name": request["target"]["name"],
                                      "target": request["target"]["code"], "info": request["overlay"]}))
            };
            results.push(batch_summary(linked, &comparison, align));
        }
        Ok(json!(results))
    }

    async fn judge_combined(&mut self, request: &mut Value) -> Result<Value, String> {
        let count = request["count"]
            .as_u64()
            .filter(|&count| (2..=8).contains(&count))
            .ok_or("invalid combined compiler batch size")? as usize;
        let name = request["target"]["name"]
            .as_str()
            .ok_or("missing batch function name")?;
        let mut object = mips_link::bytes(&request["object"])?;
        let Some(entries) = mips_batch::entries(&object, name, count) else {
            return Ok(Value::Null);
        };
        let mut reader = if request["overlay"].is_null() {
            Some(self.project_view(request)?)
        } else {
            None
        };
        let Some(comparison) = mips_diff::PreparedComparison::from_value(&request["comparison"])?
        else {
            return Ok(Value::Null);
        };
        let name = request["target"]["name"]
            .as_str()
            .ok_or("missing batch function name")?;
        let align = request["align_branches"].as_bool().unwrap_or(false);
        let mut results = Vec::with_capacity(count);
        for entry in entries {
            let offset = match entry {
                mips_batch::Entry::Local => {
                    results.push(json!({"status": "local"}));
                    continue;
                }
                mips_batch::Entry::Missing => {
                    results.push(json!({"status": "missing"}));
                    continue;
                }
                mips_batch::Entry::Ready(offset) => offset,
            };
            // The name prefix is already in the string table. Restore the suffix byte for the next candidate.
            let suffix = offset + name.len();
            let old = object[suffix];
            object[suffix] = 0;
            let linked = if let Some(reader) = reader.as_mut() {
                mips_rom::link_bytes(&object, &request["target"], reader).await
            } else {
                mips_uso::link_bytes(
                    &object,
                    &request["target"]["name"],
                    &request["target"]["code"],
                    &request["overlay"],
                )
            };
            object[suffix] = old;
            results.push(json!({"status": "ready", "suffix": suffix,
                                "judgment": batch_summary(linked, &comparison, align)}));
        }
        Ok(json!(results))
    }

    async fn perform(
        &mut self,
        mut message: Value,
        input: &mut impl BufRead,
        output: &mut impl Write,
    ) -> Result<Value, String> {
        let action = message["action"].take();
        let request = &mut message["request"];
        match action.as_str() {
            Some("ping") => Ok(json!(1)),
            Some("compare_mips") => mips_diff::evidence(request),
            Some("link_uso") => mips_uso::link(request),
            Some("rom_data_mips") => {
                mips_rom::link_data(request, &mut StdioReader { input, output }).await
            }
            Some("rom_mips") if request["callback_reader"] == true => {
                mips_rom::link(request, &mut StdioReader { input, output }).await
            }
            Some("rom_mips") => {
                let mut reader = self.project_view(request)?;
                mips_rom::link(request, &mut reader).await
            }
            Some("judge_mips_batch") => self.judge_batch(request).await,
            Some("judge_mips_combined") => self.judge_combined(request).await,
            _ => Err("unknown desktop judge operation".into()),
        }
    }
}

fn batch_summary(
    linked: Result<Value, String>,
    comparison: &mips_diff::PreparedComparison,
    align: bool,
) -> Value {
    match linked {
        Ok(link) if link["code"].is_string() => {
            match comparison.summary(&link["code"], &link["relocs"], align) {
                Ok(score) if score["score"].is_u64() => json!({
                    "code": link["code"], "relocs": link["relocs"], "exact": link["exact"],
                    "clean": link["problems"].as_array().is_some_and(Vec::is_empty),
                    "score": score["score"], "max_score": score["max_score"],
                }),
                _ => Value::Null,
            }
        }
        _ => Value::Null,
    }
}

#[tokio::main(flavor = "current_thread")]
pub async fn serve() -> i32 {
    let mut judge = Judge::default();
    let mut output = io::stdout().lock();
    let mut input = io::stdin().lock();
    let mut line = String::new();
    loop {
        match input.read_line(&mut line) {
            Ok(0) => return 0,
            Ok(_) => (),
            Err(_) => return 1,
        }
        let message = serde_json::from_str(&line);
        line.clear();
        let reply = match message {
            Ok(message) => match judge.perform(message, &mut input, &mut output).await {
                Ok(result) => json!({"result": result}),
                Err(error) => json!({"error": error}),
            },
            Err(error) => json!({"error": error.to_string()}),
        };
        if writeln!(output, "{reply}")
            .and_then(|()| output.flush())
            .is_err()
        {
            return 1;
        }
    }
}

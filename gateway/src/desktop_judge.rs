//! Private stdio transport for the desktop attempt worker.

use std::io::{self, BufRead, Write};

use serde_json::{json, Value};

use crate::{mips_diff, mips_rom, rom_project};

struct Reader<'a>(rom_project::View<'a>);

impl mips_rom::Reader for Reader<'_> {
    async fn ask(&mut self, request: Value) -> Result<Value, String> {
        self.0
            .lookup(&request)?
            .ok_or_else(|| "unsupported desktop ROM lookup".into())
    }
}

#[derive(Default)]
struct Judge {
    project: Option<(String, rom_project::Project)>,
}

impl Judge {
    async fn perform(&mut self, mut message: Value) -> Result<Value, String> {
        let action = message["action"].take();
        let request = &mut message["request"];
        match action.as_str() {
            Some("ping") => Ok(json!(1)),
            Some("compare_mips") => mips_diff::evidence(request),
            Some("rom_mips") => {
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
                let mut reader = Reader(project.view(request["rom_offset"].as_i64())?);
                mips_rom::link(request, &mut reader).await
            }
            _ => Err("unknown desktop judge operation".into()),
        }
    }
}

#[tokio::main(flavor = "current_thread")]
pub async fn serve() -> i32 {
    let mut judge = Judge::default();
    let mut output = io::stdout().lock();
    for line in io::stdin().lock().lines() {
        let message = match line {
            Ok(line) => serde_json::from_str(&line),
            Err(_) => return 1,
        };
        let reply = match message {
            Ok(message) => match judge.perform(message).await {
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
    0
}

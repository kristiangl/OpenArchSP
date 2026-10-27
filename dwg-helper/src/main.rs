use std::env;
use std::process::ExitCode;

use acadrust::{DwgReader, DxfWriter};

fn run() -> Result<(), Box<dyn std::error::Error>> {
    let arguments: Vec<String> = env::args().collect();
    if arguments.len() != 3 {
        return Err("usage: openarch-dwg-helper <input.dwg> <output.dxf>".into());
    }

    let reader = DwgReader::from_file(&arguments[1])?;
    let document = reader.read()?;
    DxfWriter::new(&document).write_to_file(&arguments[2])?;
    Ok(())
}

fn main() -> ExitCode {
    match run() {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("DWG conversion failed: {error}");
            ExitCode::FAILURE
        }
    }
}

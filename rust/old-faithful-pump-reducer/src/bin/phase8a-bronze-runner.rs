fn main() {
    let exit_code =
        old_faithful_pump_reducer::phase8a_bronze_runner_main(std::env::args_os().skip(1));
    std::process::exit(exit_code);
}

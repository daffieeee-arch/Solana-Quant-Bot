//! Hand-declared, bounded projection of Agave `confirmed_block.proto` at the
//! source identity in sources.json. Not a generated or universal status codec.
//! Unprojected nested fields remain exact bytes, as does the entire protobuf.
#[derive(Clone, PartialEq, prost::Message)]
pub struct Status {
    #[prost(message, optional, tag = "1")]
    pub err: Option<TransactionError>,
    #[prost(uint64, tag = "2")]
    pub fee: u64,
    #[prost(uint64, repeated, tag = "3")]
    pub pre_balances: Vec<u64>,
    #[prost(uint64, repeated, tag = "4")]
    pub post_balances: Vec<u64>,
    #[prost(message, repeated, tag = "5")]
    pub inner_instructions: Vec<InnerInstructions>,
    #[prost(string, repeated, tag = "6")]
    pub log_messages: Vec<String>,
    #[prost(bytes = "vec", repeated, tag = "7")]
    pub pre_token_balances_unprojected: Vec<Vec<u8>>,
    #[prost(bytes = "vec", repeated, tag = "8")]
    pub post_token_balances_unprojected: Vec<Vec<u8>>,
    #[prost(bytes = "vec", repeated, tag = "9")]
    pub rewards_unprojected: Vec<Vec<u8>>,
    #[prost(bool, tag = "10")]
    pub inner_instructions_none: bool,
    #[prost(bool, tag = "11")]
    pub log_messages_none: bool,
    #[prost(bytes = "vec", repeated, tag = "12")]
    pub loaded_writable_addresses: Vec<Vec<u8>>,
    #[prost(bytes = "vec", repeated, tag = "13")]
    pub loaded_readonly_addresses: Vec<Vec<u8>>,
    #[prost(bytes = "vec", optional, tag = "14")]
    pub return_data_unprojected: Option<Vec<u8>>,
    #[prost(bool, tag = "15")]
    pub return_data_none: bool,
    #[prost(uint64, optional, tag = "16")]
    pub compute_units_consumed: Option<u64>,
    #[prost(uint64, optional, tag = "17")]
    pub cost_units: Option<u64>,
}

#[derive(Clone, PartialEq, prost::Message)]
pub struct TransactionError {
    #[prost(bytes = "vec", tag = "1")]
    pub err: Vec<u8>,
}

#[derive(Clone, PartialEq, prost::Message)]
pub struct InnerInstructions {
    #[prost(uint32, tag = "1")]
    pub index: u32,
    #[prost(message, repeated, tag = "2")]
    pub instructions: Vec<InnerInstruction>,
}

#[derive(Clone, PartialEq, prost::Message)]
pub struct InnerInstruction {
    #[prost(uint32, tag = "1")]
    pub program_id_index: u32,
    #[prost(bytes = "vec", tag = "2")]
    pub accounts: Vec<u8>,
    #[prost(bytes = "vec", tag = "3")]
    pub data: Vec<u8>,
    #[prost(uint32, optional, tag = "4")]
    pub stack_height: Option<u32>,
}

// Generated from canonical Pydantic JSON Schemas. DO NOT EDIT.
"use strict";
exports.validatePremarketBriefingV1_2 = validate20;
const schema31 = {"$defs":{"BriefingChangeType":{"enum":["new","update","correction","contradiction"],"title":"BriefingChangeType","type":"string"},"BriefingIngestionProgressV1_2":{"additionalProperties":false,"description":"Primary-source progress carrying complete R1 1.2 lineage.","properties":{"advance_state":{"$ref":"#/$defs/SourceAdvanceState"},"batch_audit_id":{"format":"uuid","title":"Batch Audit Id","type":"string"},"business_watermark":{"anyOf":[{"format":"date-time","type":"string"},{"type":"null"}],"default":null,"title":"Business Watermark"},"capability":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Capability","type":"string"},"data_mode":{"$ref":"#/$defs/SourceDataMode"},"provenance":{"$ref":"#/$defs/SourceProvenanceV1_2"},"source_id":{"maxLength":100,"minLength":1,"title":"Source Id","type":"string"}},"required":["batch_audit_id","advance_state","data_mode","source_id","capability","provenance"],"title":"BriefingIngestionProgressV1_2","type":"object","x-runtime-invariants":["advance_state=advanced requires business_watermark"]},"InstrumentRef":{"additionalProperties":false,"description":"Market-neutral security identity suitable for future exchanges.","properties":{"asset_class":{"default":"equity","pattern":"^[a-z][a-z0-9_]{1,31}$","title":"Asset Class","type":"string"},"currency":{"default":"CNY","pattern":"^[A-Z]{3}$","title":"Currency","type":"string"},"exchange":{"pattern":"^[A-Z0-9_]{2,32}$","title":"Exchange","type":"string"},"instrument_id":{"pattern":"^[A-Z0-9_]+:[A-Za-z0-9._-]+$","title":"Instrument Id","type":"string"},"market":{"pattern":"^[A-Z0-9_]{2,32}$","title":"Market","type":"string"},"symbol":{"maxLength":64,"minLength":1,"title":"Symbol","type":"string"}},"required":["instrument_id","symbol","exchange","market"],"title":"InstrumentRef","type":"object"},"PremarketBriefingItem":{"additionalProperties":false,"description":"One evidence-linked public briefing item.","properties":{"change_type":{"$ref":"#/$defs/BriefingChangeType"},"event_id":{"format":"uuid","title":"Event Id","type":"string"},"event_revision":{"minimum":1,"title":"Event Revision","type":"integer"},"evidence_ids":{"items":{"format":"uuid","type":"string"},"maxItems":100,"minItems":1,"title":"Evidence Ids","type":"array"},"fact_summary":{"maxLength":2000,"minLength":1,"title":"Fact Summary","type":"string"},"first_seen_at":{"format":"date-time","title":"First Seen At","type":"string"},"headline":{"maxLength":300,"minLength":1,"title":"Headline","type":"string"},"inference":{"anyOf":[{"maxLength":2000,"type":"string"},{"type":"null"}],"default":null,"title":"Inference"},"published_at":{"format":"date-time","title":"Published At","type":"string"},"source_roles":{"items":{"$ref":"#/$defs/SourceRole"},"maxItems":4,"minItems":1,"title":"Source Roles","type":"array"}},"required":["event_id","event_revision","headline","fact_summary","change_type","published_at","first_seen_at","source_roles","evidence_ids"],"title":"PremarketBriefingItem","type":"object","x-runtime-invariants":["first_seen_at is >= published_at","source_roles entries are unique","evidence_ids entries are unique"]},"PremarketBriefingSection":{"additionalProperties":false,"description":"One fixed briefing coverage block.","properties":{"items":{"items":{"$ref":"#/$defs/PremarketBriefingItem"},"maxItems":100,"title":"Items","type":"array"}},"title":"PremarketBriefingSection","type":"object"},"PremarketBriefingSections":{"additionalProperties":false,"description":"Exactly the five R1 coverage blocks, each present once.","properties":{"company_industry_news":{"$ref":"#/$defs/PremarketBriefingSection"},"financial_changes":{"$ref":"#/$defs/PremarketBriefingSection"},"macro_flashes":{"$ref":"#/$defs/PremarketBriefingSection"},"market_snapshot":{"$ref":"#/$defs/PremarketBriefingSection"},"statutory_announcements":{"$ref":"#/$defs/PremarketBriefingSection"}},"required":["statutory_announcements","financial_changes","market_snapshot","company_industry_news","macro_flashes"],"title":"PremarketBriefingSections","type":"object"},"SourceAccessChannelV1_2":{"description":"R1 1.2 access channels used by replay, free and authorized adapters.","enum":["demo_replay","akshare","official_http","baostock_sdk","authorized_api"],"title":"SourceAccessChannelV1_2","type":"string"},"SourceAdvanceState":{"description":"Provider-neutral result of the most recent source batch.","enum":["advanced","not_advanced"],"title":"SourceAdvanceState","type":"string"},"SourceCoverageStatus":{"description":"Public source capability coverage states.","enum":["ok","delayed","unavailable","rights_blocked","not_qualified","timeliness_unverified"],"title":"SourceCoverageStatus","type":"string"},"SourceCoverageV1_2":{"additionalProperties":false,"description":"Source capability coverage carrying complete R1 1.2 lineage.","properties":{"capability":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Capability","type":"string"},"gap_reason":{"anyOf":[{"maxLength":500,"minLength":1,"type":"string"},{"type":"null"}],"default":null,"title":"Gap Reason"},"last_success_at":{"anyOf":[{"format":"date-time","type":"string"},{"type":"null"}],"default":null,"title":"Last Success At"},"license_id":{"maxLength":100,"minLength":1,"title":"License Id","type":"string"},"observed_at":{"format":"date-time","title":"Observed At","type":"string"},"progress":{"$ref":"#/$defs/SourceIngestionProgress"},"provenance":{"$ref":"#/$defs/SourceProvenanceV1_2"},"source_id":{"maxLength":100,"minLength":1,"title":"Source Id","type":"string"},"source_role":{"$ref":"#/$defs/SourceRole"},"status":{"$ref":"#/$defs/SourceCoverageStatus"}},"required":["source_id","capability","source_role","status","observed_at","license_id","progress","provenance"],"title":"SourceCoverageV1_2","type":"object","x-runtime-invariants":["last_success_at is <= observed_at when present","status=ok requires last_success_at","status other than ok requires gap_reason","progress.business_watermark is <= observed_at when present","progress.advance_state=advanced requires last_success_at"]},"SourceDataMode":{"description":"Whether source evidence is real or restricted to development fixtures.","enum":["real","synthetic_development_only"],"title":"SourceDataMode","type":"string"},"SourceEvidenceRole":{"description":"How one source may support a fact without changing its source identity.","enum":["authoritative","structured_helper","discovery","cross_check"],"title":"SourceEvidenceRole","type":"string"},"SourceIngestionProgress":{"additionalProperties":false,"description":"Public provider-neutral ingestion progress metadata.","properties":{"advance_state":{"$ref":"#/$defs/SourceAdvanceState"},"batch_audit_id":{"format":"uuid","title":"Batch Audit Id","type":"string"},"business_watermark":{"anyOf":[{"format":"date-time","type":"string"},{"type":"null"}],"default":null,"title":"Business Watermark"},"data_mode":{"$ref":"#/$defs/SourceDataMode"}},"required":["batch_audit_id","advance_state","data_mode"],"title":"SourceIngestionProgress","type":"object","x-runtime-invariants":["advance_state=advanced requires business_watermark"]},"SourceProvenanceV1_2":{"additionalProperties":false,"allOf":[{"if":{"properties":{"runtime_mode":{"const":"demo_replay"}}},"then":{"properties":{"access_channel":{"const":"demo_replay"},"quality_tier":{"const":"demo_replay"},"release_tier":{"const":"dev_only"},"rights_status":{"const":"not_applicable"}}}},{"if":{"properties":{"runtime_mode":{"const":"public_static"}}},"then":{"properties":{"access_channel":{"const":"demo_replay"},"quality_tier":{"const":"demo_replay"},"release_tier":{"const":"dev_only"},"rights_status":{"const":"not_applicable"}}}},{"if":{"properties":{"runtime_mode":{"const":"dev_live"}}},"then":{"properties":{"access_channel":{"enum":["akshare","official_http","baostock_sdk"]},"quality_tier":{"enum":["free_official","free_structured","dev_web"]},"release_tier":{"const":"dev_only"},"rights_status":{"const":"unverified"}}}},{"if":{"properties":{"runtime_mode":{"const":"authorized"}}},"then":{"properties":{"access_channel":{"const":"authorized_api"},"quality_tier":{"const":"authorized"},"release_tier":{"const":"authorized"},"rights_status":{"const":"approved"}}}}],"description":"R1 1.2 lineage with explicit quality and evidence-role dimensions.","properties":{"access_channel":{"$ref":"#/$defs/SourceAccessChannelV1_2"},"adapter_name":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Adapter Name","type":"string"},"adapter_version":{"maxLength":100,"minLength":1,"title":"Adapter Version","type":"string"},"endpoint_name":{"pattern":"^[a-z][a-z0-9_.-]{1,199}$","title":"Endpoint Name","type":"string"},"evidence_role":{"$ref":"#/$defs/SourceEvidenceRole"},"provider":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Provider","type":"string"},"quality_tier":{"$ref":"#/$defs/SourceQualityTier"},"release_tier":{"$ref":"#/$defs/SourceReleaseTier"},"rights_status":{"$ref":"#/$defs/SourceRightsStatusV1_2"},"runtime_mode":{"$ref":"#/$defs/SourceRuntimeModeV1_2"},"schema_version":{"const":"1.2","default":"1.2","title":"Schema Version","type":"string"},"source_identifier":{"maxLength":500,"minLength":1,"title":"Source Identifier","type":"string"}},"required":["schema_version","provider","access_channel","adapter_name","adapter_version","endpoint_name","source_identifier","runtime_mode","release_tier","rights_status","quality_tier","evidence_role"],"title":"SourceProvenanceV1_2","type":"object","x-runtime-invariants":["runtime_mode constrains channel, release tier, rights status, and quality","quality_tier and evidence_role remain independent dimensions"]},"SourceQualityTier":{"description":"Coarse quality origin kept separate from evidence authority and rights.","enum":["demo_replay","free_official","free_structured","dev_web","authorized"],"title":"SourceQualityTier","type":"string"},"SourceReleaseTier":{"description":"Whether a source result may leave development-only surfaces.","enum":["dev_only","authorized"],"title":"SourceReleaseTier","type":"string"},"SourceRightsStatusV1_2":{"description":"R1 1.2 public rights state, including data with no source-rights claim.","enum":["unverified","approved","blocked","not_applicable"],"title":"SourceRightsStatusV1_2","type":"string"},"SourceRole":{"description":"Permitted public source roles.","enum":["statutory","professional_market","professional_information","discovery"],"title":"SourceRole","type":"string"},"SourceRuntimeModeV1_2":{"description":"R1 1.2 execution modes, including the detached public snapshot.","enum":["demo_replay","dev_live","public_static","authorized"],"title":"SourceRuntimeModeV1_2","type":"string"}},"$id":"https://stock-market-agent.local/schemas/briefings/premarket-view.v1.2.schema.json","$schema":"https://json-schema.org/draft/2020-12/schema","additionalProperties":false,"description":"R1 1.2 read model with explicit static/free source provenance.","properties":{"as_of":{"format":"date-time","title":"As Of","type":"string"},"briefing_id":{"format":"uuid","title":"Briefing Id","type":"string"},"comparison_since":{"format":"date-time","title":"Comparison Since","type":"string"},"disclaimer":{"default":"本系统仅供个人信息整理与研究，不构成证券投资建议、收益承诺或交易指令。","title":"Disclaimer","type":"string"},"generated_at":{"format":"date-time","title":"Generated At","type":"string"},"ingestion_progress":{"$ref":"#/$defs/BriefingIngestionProgressV1_2"},"instrument":{"$ref":"#/$defs/InstrumentRef"},"omitted_duplicate_count":{"default":0,"minimum":0,"title":"Omitted Duplicate Count","type":"integer"},"release_tier":{"$ref":"#/$defs/SourceReleaseTier"},"ruleset_version":{"maxLength":100,"minLength":1,"title":"Ruleset Version","type":"string"},"runtime_mode":{"$ref":"#/$defs/SourceRuntimeModeV1_2"},"schema_version":{"const":"1.2","default":"1.2","title":"Schema Version","type":"string"},"sections":{"$ref":"#/$defs/PremarketBriefingSections"},"source_coverage":{"items":{"$ref":"#/$defs/SourceCoverageV1_2"},"maxItems":200,"title":"Source Coverage","type":"array"}},"required":["schema_version","instrument","comparison_since","as_of","generated_at","sections","ingestion_progress","ruleset_version","runtime_mode","release_tier"],"title":"PremarketBriefingViewV1_2","type":"object","x-contract-model":"stock_assistant.contracts.premarket_views.PremarketBriefingViewV1_2","x-runtime-invariants":["runtime_mode determines ingestion_progress.data_mode","all 1.2 provenance runtime modes and release tiers match the briefing"],"x-schema-version":"1.2"};
const schema42 = {"additionalProperties":false,"description":"Market-neutral security identity suitable for future exchanges.","properties":{"asset_class":{"default":"equity","pattern":"^[a-z][a-z0-9_]{1,31}$","title":"Asset Class","type":"string"},"currency":{"default":"CNY","pattern":"^[A-Z]{3}$","title":"Currency","type":"string"},"exchange":{"pattern":"^[A-Z0-9_]{2,32}$","title":"Exchange","type":"string"},"instrument_id":{"pattern":"^[A-Z0-9_]+:[A-Za-z0-9._-]+$","title":"Instrument Id","type":"string"},"market":{"pattern":"^[A-Z0-9_]{2,32}$","title":"Market","type":"string"},"symbol":{"maxLength":64,"minLength":1,"title":"Symbol","type":"string"}},"required":["instrument_id","symbol","exchange","market"],"title":"InstrumentRef","type":"object"};
const schema39 = {"description":"Whether a source result may leave development-only surfaces.","enum":["dev_only","authorized"],"title":"SourceReleaseTier","type":"string"};
const schema41 = {"description":"R1 1.2 execution modes, including the detached public snapshot.","enum":["demo_replay","dev_live","public_static","authorized"],"title":"SourceRuntimeModeV1_2","type":"string"};
const func1 = Object.prototype.hasOwnProperty;
const func3 = require("@redocly/ajv/dist/runtime/ucs2length").default;
const formats0 = require("./public-v12-formats.generated.cjs")["date-time"];
const formats2 = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const schema32 = {"additionalProperties":false,"description":"Primary-source progress carrying complete R1 1.2 lineage.","properties":{"advance_state":{"$ref":"#/$defs/SourceAdvanceState"},"batch_audit_id":{"format":"uuid","title":"Batch Audit Id","type":"string"},"business_watermark":{"anyOf":[{"format":"date-time","type":"string"},{"type":"null"}],"default":null,"title":"Business Watermark"},"capability":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Capability","type":"string"},"data_mode":{"$ref":"#/$defs/SourceDataMode"},"provenance":{"$ref":"#/$defs/SourceProvenanceV1_2"},"source_id":{"maxLength":100,"minLength":1,"title":"Source Id","type":"string"}},"required":["batch_audit_id","advance_state","data_mode","source_id","capability","provenance"],"title":"BriefingIngestionProgressV1_2","type":"object","x-runtime-invariants":["advance_state=advanced requires business_watermark"]};
const schema33 = {"description":"Provider-neutral result of the most recent source batch.","enum":["advanced","not_advanced"],"title":"SourceAdvanceState","type":"string"};
const schema34 = {"description":"Whether source evidence is real or restricted to development fixtures.","enum":["real","synthetic_development_only"],"title":"SourceDataMode","type":"string"};
const pattern4 = new RegExp("^[a-z][a-z0-9_.-]{1,99}$", "u");
const schema35 = {"additionalProperties":false,"allOf":[{"if":{"properties":{"runtime_mode":{"const":"demo_replay"}}},"then":{"properties":{"access_channel":{"const":"demo_replay"},"quality_tier":{"const":"demo_replay"},"release_tier":{"const":"dev_only"},"rights_status":{"const":"not_applicable"}}}},{"if":{"properties":{"runtime_mode":{"const":"public_static"}}},"then":{"properties":{"access_channel":{"const":"demo_replay"},"quality_tier":{"const":"demo_replay"},"release_tier":{"const":"dev_only"},"rights_status":{"const":"not_applicable"}}}},{"if":{"properties":{"runtime_mode":{"const":"dev_live"}}},"then":{"properties":{"access_channel":{"enum":["akshare","official_http","baostock_sdk"]},"quality_tier":{"enum":["free_official","free_structured","dev_web"]},"release_tier":{"const":"dev_only"},"rights_status":{"const":"unverified"}}}},{"if":{"properties":{"runtime_mode":{"const":"authorized"}}},"then":{"properties":{"access_channel":{"const":"authorized_api"},"quality_tier":{"const":"authorized"},"release_tier":{"const":"authorized"},"rights_status":{"const":"approved"}}}}],"description":"R1 1.2 lineage with explicit quality and evidence-role dimensions.","properties":{"access_channel":{"$ref":"#/$defs/SourceAccessChannelV1_2"},"adapter_name":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Adapter Name","type":"string"},"adapter_version":{"maxLength":100,"minLength":1,"title":"Adapter Version","type":"string"},"endpoint_name":{"pattern":"^[a-z][a-z0-9_.-]{1,199}$","title":"Endpoint Name","type":"string"},"evidence_role":{"$ref":"#/$defs/SourceEvidenceRole"},"provider":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Provider","type":"string"},"quality_tier":{"$ref":"#/$defs/SourceQualityTier"},"release_tier":{"$ref":"#/$defs/SourceReleaseTier"},"rights_status":{"$ref":"#/$defs/SourceRightsStatusV1_2"},"runtime_mode":{"$ref":"#/$defs/SourceRuntimeModeV1_2"},"schema_version":{"const":"1.2","default":"1.2","title":"Schema Version","type":"string"},"source_identifier":{"maxLength":500,"minLength":1,"title":"Source Identifier","type":"string"}},"required":["schema_version","provider","access_channel","adapter_name","adapter_version","endpoint_name","source_identifier","runtime_mode","release_tier","rights_status","quality_tier","evidence_role"],"title":"SourceProvenanceV1_2","type":"object","x-runtime-invariants":["runtime_mode constrains channel, release tier, rights status, and quality","quality_tier and evidence_role remain independent dimensions"]};
const schema36 = {"description":"R1 1.2 access channels used by replay, free and authorized adapters.","enum":["demo_replay","akshare","official_http","baostock_sdk","authorized_api"],"title":"SourceAccessChannelV1_2","type":"string"};
const schema37 = {"description":"How one source may support a fact without changing its source identity.","enum":["authoritative","structured_helper","discovery","cross_check"],"title":"SourceEvidenceRole","type":"string"};
const schema38 = {"description":"Coarse quality origin kept separate from evidence authority and rights.","enum":["demo_replay","free_official","free_structured","dev_web","authorized"],"title":"SourceQualityTier","type":"string"};
const schema40 = {"description":"R1 1.2 public rights state, including data with no source-rights claim.","enum":["unverified","approved","blocked","not_applicable"],"title":"SourceRightsStatusV1_2","type":"string"};
const pattern6 = new RegExp("^[a-z][a-z0-9_.-]{1,199}$", "u");

function validate22(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}, isAllOfVariant = 0}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate22.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
const _errs2 = errors;
let valid1 = true;
const _errs3 = errors;
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.runtime_mode !== undefined){
if("demo_replay" !== data.runtime_mode){
const err0 = {};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
}
}
var _valid0 = _errs3 === errors;
errors = _errs2;
if(vErrors !== null){
if(_errs2){
vErrors.length = _errs2;
}
else {
vErrors = null;
}
}
if(_valid0){
const _errs5 = errors;
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.access_channel !== undefined){
if("demo_replay" !== data.access_channel){
const err1 = {instancePath:instancePath+"/access_channel",schemaPath:"#/allOf/0/then/properties/access_channel/const",keyword:"const",params:{allowedValue: "demo_replay"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
}
if(data.quality_tier !== undefined){
if("demo_replay" !== data.quality_tier){
const err2 = {instancePath:instancePath+"/quality_tier",schemaPath:"#/allOf/0/then/properties/quality_tier/const",keyword:"const",params:{allowedValue: "demo_replay"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
}
if(data.release_tier !== undefined){
if("dev_only" !== data.release_tier){
const err3 = {instancePath:instancePath+"/release_tier",schemaPath:"#/allOf/0/then/properties/release_tier/const",keyword:"const",params:{allowedValue: "dev_only"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
}
}
if(data.rights_status !== undefined){
if("not_applicable" !== data.rights_status){
const err4 = {instancePath:instancePath+"/rights_status",schemaPath:"#/allOf/0/then/properties/rights_status/const",keyword:"const",params:{allowedValue: "not_applicable"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err4];
}
else {
vErrors.push(err4);
}
errors++;
}
}
}
var _valid0 = _errs5 === errors;
valid1 = _valid0;
if(valid1){
var props0 = {};
props0.access_channel = true;
props0.quality_tier = true;
props0.release_tier = true;
props0.rights_status = true;
props0.runtime_mode = true;
}
}
if(!valid1){
const err5 = {instancePath,schemaPath:"#/allOf/0/if",keyword:"if",params:{failingKeyword: "then"},message:"must match \"then\" schema"};
if(vErrors === null){
vErrors = [err5];
}
else {
vErrors.push(err5);
}
errors++;
}
const _errs11 = errors;
let valid4 = true;
const _errs12 = errors;
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.runtime_mode !== undefined){
if("public_static" !== data.runtime_mode){
const err6 = {};
if(vErrors === null){
vErrors = [err6];
}
else {
vErrors.push(err6);
}
errors++;
}
}
}
var _valid1 = _errs12 === errors;
errors = _errs11;
if(vErrors !== null){
if(_errs11){
vErrors.length = _errs11;
}
else {
vErrors = null;
}
}
if(_valid1){
const _errs14 = errors;
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.access_channel !== undefined){
if("demo_replay" !== data.access_channel){
const err7 = {instancePath:instancePath+"/access_channel",schemaPath:"#/allOf/1/then/properties/access_channel/const",keyword:"const",params:{allowedValue: "demo_replay"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err7];
}
else {
vErrors.push(err7);
}
errors++;
}
}
if(data.quality_tier !== undefined){
if("demo_replay" !== data.quality_tier){
const err8 = {instancePath:instancePath+"/quality_tier",schemaPath:"#/allOf/1/then/properties/quality_tier/const",keyword:"const",params:{allowedValue: "demo_replay"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err8];
}
else {
vErrors.push(err8);
}
errors++;
}
}
if(data.release_tier !== undefined){
if("dev_only" !== data.release_tier){
const err9 = {instancePath:instancePath+"/release_tier",schemaPath:"#/allOf/1/then/properties/release_tier/const",keyword:"const",params:{allowedValue: "dev_only"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err9];
}
else {
vErrors.push(err9);
}
errors++;
}
}
if(data.rights_status !== undefined){
if("not_applicable" !== data.rights_status){
const err10 = {instancePath:instancePath+"/rights_status",schemaPath:"#/allOf/1/then/properties/rights_status/const",keyword:"const",params:{allowedValue: "not_applicable"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err10];
}
else {
vErrors.push(err10);
}
errors++;
}
}
}
var _valid1 = _errs14 === errors;
valid4 = _valid1;
if(valid4){
var props1 = {};
props1.access_channel = true;
props1.quality_tier = true;
props1.release_tier = true;
props1.rights_status = true;
props1.runtime_mode = true;
}
}
if(!valid4){
const err11 = {instancePath,schemaPath:"#/allOf/1/if",keyword:"if",params:{failingKeyword: "then"},message:"must match \"then\" schema"};
if(vErrors === null){
vErrors = [err11];
}
else {
vErrors.push(err11);
}
errors++;
}
if(props0 !== true && props1 !== undefined){
if(props1 === true){
props0 = true;
}
else {
props0 = props0 || {};
Object.assign(props0, props1);
}
}
const _errs20 = errors;
let valid7 = true;
const _errs21 = errors;
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.runtime_mode !== undefined){
if("dev_live" !== data.runtime_mode){
const err12 = {};
if(vErrors === null){
vErrors = [err12];
}
else {
vErrors.push(err12);
}
errors++;
}
}
}
var _valid2 = _errs21 === errors;
errors = _errs20;
if(vErrors !== null){
if(_errs20){
vErrors.length = _errs20;
}
else {
vErrors = null;
}
}
if(_valid2){
const _errs23 = errors;
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.access_channel !== undefined){
let data11 = data.access_channel;
if(!(((data11 === "akshare") || (data11 === "official_http")) || (data11 === "baostock_sdk"))){
const err13 = {instancePath:instancePath+"/access_channel",schemaPath:"#/allOf/2/then/properties/access_channel/enum",keyword:"enum",params:{allowedValues: schema35.allOf[2].then.properties.access_channel.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err13];
}
else {
vErrors.push(err13);
}
errors++;
}
}
if(data.quality_tier !== undefined){
let data12 = data.quality_tier;
if(!(((data12 === "free_official") || (data12 === "free_structured")) || (data12 === "dev_web"))){
const err14 = {instancePath:instancePath+"/quality_tier",schemaPath:"#/allOf/2/then/properties/quality_tier/enum",keyword:"enum",params:{allowedValues: schema35.allOf[2].then.properties.quality_tier.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err14];
}
else {
vErrors.push(err14);
}
errors++;
}
}
if(data.release_tier !== undefined){
if("dev_only" !== data.release_tier){
const err15 = {instancePath:instancePath+"/release_tier",schemaPath:"#/allOf/2/then/properties/release_tier/const",keyword:"const",params:{allowedValue: "dev_only"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err15];
}
else {
vErrors.push(err15);
}
errors++;
}
}
if(data.rights_status !== undefined){
if("unverified" !== data.rights_status){
const err16 = {instancePath:instancePath+"/rights_status",schemaPath:"#/allOf/2/then/properties/rights_status/const",keyword:"const",params:{allowedValue: "unverified"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err16];
}
else {
vErrors.push(err16);
}
errors++;
}
}
}
var _valid2 = _errs23 === errors;
valid7 = _valid2;
if(valid7){
var props2 = {};
props2.access_channel = true;
props2.quality_tier = true;
props2.release_tier = true;
props2.rights_status = true;
props2.runtime_mode = true;
}
}
if(!valid7){
const err17 = {instancePath,schemaPath:"#/allOf/2/if",keyword:"if",params:{failingKeyword: "then"},message:"must match \"then\" schema"};
if(vErrors === null){
vErrors = [err17];
}
else {
vErrors.push(err17);
}
errors++;
}
if(props0 !== true && props2 !== undefined){
if(props2 === true){
props0 = true;
}
else {
props0 = props0 || {};
Object.assign(props0, props2);
}
}
const _errs29 = errors;
let valid10 = true;
const _errs30 = errors;
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.runtime_mode !== undefined){
if("authorized" !== data.runtime_mode){
const err18 = {};
if(vErrors === null){
vErrors = [err18];
}
else {
vErrors.push(err18);
}
errors++;
}
}
}
var _valid3 = _errs30 === errors;
errors = _errs29;
if(vErrors !== null){
if(_errs29){
vErrors.length = _errs29;
}
else {
vErrors = null;
}
}
if(_valid3){
const _errs32 = errors;
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.access_channel !== undefined){
if("authorized_api" !== data.access_channel){
const err19 = {instancePath:instancePath+"/access_channel",schemaPath:"#/allOf/3/then/properties/access_channel/const",keyword:"const",params:{allowedValue: "authorized_api"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err19];
}
else {
vErrors.push(err19);
}
errors++;
}
}
if(data.quality_tier !== undefined){
if("authorized" !== data.quality_tier){
const err20 = {instancePath:instancePath+"/quality_tier",schemaPath:"#/allOf/3/then/properties/quality_tier/const",keyword:"const",params:{allowedValue: "authorized"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err20];
}
else {
vErrors.push(err20);
}
errors++;
}
}
if(data.release_tier !== undefined){
if("authorized" !== data.release_tier){
const err21 = {instancePath:instancePath+"/release_tier",schemaPath:"#/allOf/3/then/properties/release_tier/const",keyword:"const",params:{allowedValue: "authorized"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err21];
}
else {
vErrors.push(err21);
}
errors++;
}
}
if(data.rights_status !== undefined){
if("approved" !== data.rights_status){
const err22 = {instancePath:instancePath+"/rights_status",schemaPath:"#/allOf/3/then/properties/rights_status/const",keyword:"const",params:{allowedValue: "approved"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err22];
}
else {
vErrors.push(err22);
}
errors++;
}
}
}
var _valid3 = _errs32 === errors;
valid10 = _valid3;
if(valid10){
var props3 = {};
props3.access_channel = true;
props3.quality_tier = true;
props3.release_tier = true;
props3.rights_status = true;
props3.runtime_mode = true;
}
}
if(!valid10){
const err23 = {instancePath,schemaPath:"#/allOf/3/if",keyword:"if",params:{failingKeyword: "then"},message:"must match \"then\" schema"};
if(vErrors === null){
vErrors = [err23];
}
else {
vErrors.push(err23);
}
errors++;
}
if(props0 !== true && props3 !== undefined){
if(props3 === true){
props0 = true;
}
else {
props0 = props0 || {};
Object.assign(props0, props3);
}
}
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.schema_version === undefined){
const err24 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "schema_version"},message:"must have required property '"+"schema_version"+"'"};
if(vErrors === null){
vErrors = [err24];
}
else {
vErrors.push(err24);
}
errors++;
}
if(data.provider === undefined){
const err25 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "provider"},message:"must have required property '"+"provider"+"'"};
if(vErrors === null){
vErrors = [err25];
}
else {
vErrors.push(err25);
}
errors++;
}
if(data.access_channel === undefined){
const err26 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "access_channel"},message:"must have required property '"+"access_channel"+"'"};
if(vErrors === null){
vErrors = [err26];
}
else {
vErrors.push(err26);
}
errors++;
}
if(data.adapter_name === undefined){
const err27 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "adapter_name"},message:"must have required property '"+"adapter_name"+"'"};
if(vErrors === null){
vErrors = [err27];
}
else {
vErrors.push(err27);
}
errors++;
}
if(data.adapter_version === undefined){
const err28 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "adapter_version"},message:"must have required property '"+"adapter_version"+"'"};
if(vErrors === null){
vErrors = [err28];
}
else {
vErrors.push(err28);
}
errors++;
}
if(data.endpoint_name === undefined){
const err29 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "endpoint_name"},message:"must have required property '"+"endpoint_name"+"'"};
if(vErrors === null){
vErrors = [err29];
}
else {
vErrors.push(err29);
}
errors++;
}
if(data.source_identifier === undefined){
const err30 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "source_identifier"},message:"must have required property '"+"source_identifier"+"'"};
if(vErrors === null){
vErrors = [err30];
}
else {
vErrors.push(err30);
}
errors++;
}
if(data.runtime_mode === undefined){
const err31 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "runtime_mode"},message:"must have required property '"+"runtime_mode"+"'"};
if(vErrors === null){
vErrors = [err31];
}
else {
vErrors.push(err31);
}
errors++;
}
if(data.release_tier === undefined){
const err32 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "release_tier"},message:"must have required property '"+"release_tier"+"'"};
if(vErrors === null){
vErrors = [err32];
}
else {
vErrors.push(err32);
}
errors++;
}
if(data.rights_status === undefined){
const err33 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "rights_status"},message:"must have required property '"+"rights_status"+"'"};
if(vErrors === null){
vErrors = [err33];
}
else {
vErrors.push(err33);
}
errors++;
}
if(data.quality_tier === undefined){
const err34 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "quality_tier"},message:"must have required property '"+"quality_tier"+"'"};
if(vErrors === null){
vErrors = [err34];
}
else {
vErrors.push(err34);
}
errors++;
}
if(data.evidence_role === undefined){
const err35 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "evidence_role"},message:"must have required property '"+"evidence_role"+"'"};
if(vErrors === null){
vErrors = [err35];
}
else {
vErrors.push(err35);
}
errors++;
}
for(const key0 in data){
if(!(func1.call(schema35.properties, key0))){
const err36 = {instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err36];
}
else {
vErrors.push(err36);
}
errors++;
}
}
if(data.access_channel !== undefined){
let data20 = data.access_channel;
if(typeof data20 !== "string"){
const err37 = {instancePath:instancePath+"/access_channel",schemaPath:"#/$defs/SourceAccessChannelV1_2/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err37];
}
else {
vErrors.push(err37);
}
errors++;
}
if(!(((((data20 === "demo_replay") || (data20 === "akshare")) || (data20 === "official_http")) || (data20 === "baostock_sdk")) || (data20 === "authorized_api"))){
const err38 = {instancePath:instancePath+"/access_channel",schemaPath:"#/$defs/SourceAccessChannelV1_2/enum",keyword:"enum",params:{allowedValues: schema36.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err38];
}
else {
vErrors.push(err38);
}
errors++;
}
}
if(data.adapter_name !== undefined){
let data21 = data.adapter_name;
if(typeof data21 === "string"){
if(!pattern4.test(data21)){
const err39 = {instancePath:instancePath+"/adapter_name",schemaPath:"#/properties/adapter_name/pattern",keyword:"pattern",params:{pattern: "^[a-z][a-z0-9_.-]{1,99}$"},message:"must match pattern \""+"^[a-z][a-z0-9_.-]{1,99}$"+"\""};
if(vErrors === null){
vErrors = [err39];
}
else {
vErrors.push(err39);
}
errors++;
}
}
else {
const err40 = {instancePath:instancePath+"/adapter_name",schemaPath:"#/properties/adapter_name/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err40];
}
else {
vErrors.push(err40);
}
errors++;
}
}
if(data.adapter_version !== undefined){
let data22 = data.adapter_version;
if(typeof data22 === "string"){
if(func3(data22) > 100){
const err41 = {instancePath:instancePath+"/adapter_version",schemaPath:"#/properties/adapter_version/maxLength",keyword:"maxLength",params:{limit: 100},message:"must NOT have more than 100 characters"};
if(vErrors === null){
vErrors = [err41];
}
else {
vErrors.push(err41);
}
errors++;
}
if(func3(data22) < 1){
const err42 = {instancePath:instancePath+"/adapter_version",schemaPath:"#/properties/adapter_version/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err42];
}
else {
vErrors.push(err42);
}
errors++;
}
}
else {
const err43 = {instancePath:instancePath+"/adapter_version",schemaPath:"#/properties/adapter_version/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err43];
}
else {
vErrors.push(err43);
}
errors++;
}
}
if(data.endpoint_name !== undefined){
let data23 = data.endpoint_name;
if(typeof data23 === "string"){
if(!pattern6.test(data23)){
const err44 = {instancePath:instancePath+"/endpoint_name",schemaPath:"#/properties/endpoint_name/pattern",keyword:"pattern",params:{pattern: "^[a-z][a-z0-9_.-]{1,199}$"},message:"must match pattern \""+"^[a-z][a-z0-9_.-]{1,199}$"+"\""};
if(vErrors === null){
vErrors = [err44];
}
else {
vErrors.push(err44);
}
errors++;
}
}
else {
const err45 = {instancePath:instancePath+"/endpoint_name",schemaPath:"#/properties/endpoint_name/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err45];
}
else {
vErrors.push(err45);
}
errors++;
}
}
if(data.evidence_role !== undefined){
let data24 = data.evidence_role;
if(typeof data24 !== "string"){
const err46 = {instancePath:instancePath+"/evidence_role",schemaPath:"#/$defs/SourceEvidenceRole/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err46];
}
else {
vErrors.push(err46);
}
errors++;
}
if(!((((data24 === "authoritative") || (data24 === "structured_helper")) || (data24 === "discovery")) || (data24 === "cross_check"))){
const err47 = {instancePath:instancePath+"/evidence_role",schemaPath:"#/$defs/SourceEvidenceRole/enum",keyword:"enum",params:{allowedValues: schema37.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err47];
}
else {
vErrors.push(err47);
}
errors++;
}
}
if(data.provider !== undefined){
let data25 = data.provider;
if(typeof data25 === "string"){
if(!pattern4.test(data25)){
const err48 = {instancePath:instancePath+"/provider",schemaPath:"#/properties/provider/pattern",keyword:"pattern",params:{pattern: "^[a-z][a-z0-9_.-]{1,99}$"},message:"must match pattern \""+"^[a-z][a-z0-9_.-]{1,99}$"+"\""};
if(vErrors === null){
vErrors = [err48];
}
else {
vErrors.push(err48);
}
errors++;
}
}
else {
const err49 = {instancePath:instancePath+"/provider",schemaPath:"#/properties/provider/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err49];
}
else {
vErrors.push(err49);
}
errors++;
}
}
if(data.quality_tier !== undefined){
let data26 = data.quality_tier;
if(typeof data26 !== "string"){
const err50 = {instancePath:instancePath+"/quality_tier",schemaPath:"#/$defs/SourceQualityTier/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err50];
}
else {
vErrors.push(err50);
}
errors++;
}
if(!(((((data26 === "demo_replay") || (data26 === "free_official")) || (data26 === "free_structured")) || (data26 === "dev_web")) || (data26 === "authorized"))){
const err51 = {instancePath:instancePath+"/quality_tier",schemaPath:"#/$defs/SourceQualityTier/enum",keyword:"enum",params:{allowedValues: schema38.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err51];
}
else {
vErrors.push(err51);
}
errors++;
}
}
if(data.release_tier !== undefined){
let data27 = data.release_tier;
if(typeof data27 !== "string"){
const err52 = {instancePath:instancePath+"/release_tier",schemaPath:"#/$defs/SourceReleaseTier/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err52];
}
else {
vErrors.push(err52);
}
errors++;
}
if(!((data27 === "dev_only") || (data27 === "authorized"))){
const err53 = {instancePath:instancePath+"/release_tier",schemaPath:"#/$defs/SourceReleaseTier/enum",keyword:"enum",params:{allowedValues: schema39.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err53];
}
else {
vErrors.push(err53);
}
errors++;
}
}
if(data.rights_status !== undefined){
let data28 = data.rights_status;
if(typeof data28 !== "string"){
const err54 = {instancePath:instancePath+"/rights_status",schemaPath:"#/$defs/SourceRightsStatusV1_2/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err54];
}
else {
vErrors.push(err54);
}
errors++;
}
if(!((((data28 === "unverified") || (data28 === "approved")) || (data28 === "blocked")) || (data28 === "not_applicable"))){
const err55 = {instancePath:instancePath+"/rights_status",schemaPath:"#/$defs/SourceRightsStatusV1_2/enum",keyword:"enum",params:{allowedValues: schema40.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err55];
}
else {
vErrors.push(err55);
}
errors++;
}
}
if(data.runtime_mode !== undefined){
let data29 = data.runtime_mode;
if(typeof data29 !== "string"){
const err56 = {instancePath:instancePath+"/runtime_mode",schemaPath:"#/$defs/SourceRuntimeModeV1_2/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err56];
}
else {
vErrors.push(err56);
}
errors++;
}
if(!((((data29 === "demo_replay") || (data29 === "dev_live")) || (data29 === "public_static")) || (data29 === "authorized"))){
const err57 = {instancePath:instancePath+"/runtime_mode",schemaPath:"#/$defs/SourceRuntimeModeV1_2/enum",keyword:"enum",params:{allowedValues: schema41.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err57];
}
else {
vErrors.push(err57);
}
errors++;
}
}
if(data.schema_version !== undefined){
let data30 = data.schema_version;
if(typeof data30 !== "string"){
const err58 = {instancePath:instancePath+"/schema_version",schemaPath:"#/properties/schema_version/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err58];
}
else {
vErrors.push(err58);
}
errors++;
}
if("1.2" !== data30){
const err59 = {instancePath:instancePath+"/schema_version",schemaPath:"#/properties/schema_version/const",keyword:"const",params:{allowedValue: "1.2"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err59];
}
else {
vErrors.push(err59);
}
errors++;
}
}
if(data.source_identifier !== undefined){
let data31 = data.source_identifier;
if(typeof data31 === "string"){
if(func3(data31) > 500){
const err60 = {instancePath:instancePath+"/source_identifier",schemaPath:"#/properties/source_identifier/maxLength",keyword:"maxLength",params:{limit: 500},message:"must NOT have more than 500 characters"};
if(vErrors === null){
vErrors = [err60];
}
else {
vErrors.push(err60);
}
errors++;
}
if(func3(data31) < 1){
const err61 = {instancePath:instancePath+"/source_identifier",schemaPath:"#/properties/source_identifier/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err61];
}
else {
vErrors.push(err61);
}
errors++;
}
}
else {
const err62 = {instancePath:instancePath+"/source_identifier",schemaPath:"#/properties/source_identifier/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err62];
}
else {
vErrors.push(err62);
}
errors++;
}
}
}
else {
const err63 = {instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err63];
}
else {
vErrors.push(err63);
}
errors++;
}
validate22.errors = vErrors;
return errors === 0;
}
validate22.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};


function validate21(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}, isAllOfVariant = 0}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate21.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.batch_audit_id === undefined){
const err0 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "batch_audit_id"},message:"must have required property '"+"batch_audit_id"+"'"};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
if(data.advance_state === undefined){
const err1 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "advance_state"},message:"must have required property '"+"advance_state"+"'"};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
if(data.data_mode === undefined){
const err2 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "data_mode"},message:"must have required property '"+"data_mode"+"'"};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
if(data.source_id === undefined){
const err3 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "source_id"},message:"must have required property '"+"source_id"+"'"};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
}
if(data.capability === undefined){
const err4 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "capability"},message:"must have required property '"+"capability"+"'"};
if(vErrors === null){
vErrors = [err4];
}
else {
vErrors.push(err4);
}
errors++;
}
if(data.provenance === undefined){
const err5 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "provenance"},message:"must have required property '"+"provenance"+"'"};
if(vErrors === null){
vErrors = [err5];
}
else {
vErrors.push(err5);
}
errors++;
}
for(const key0 in data){
if(!(((((((key0 === "advance_state") || (key0 === "batch_audit_id")) || (key0 === "business_watermark")) || (key0 === "capability")) || (key0 === "data_mode")) || (key0 === "provenance")) || (key0 === "source_id"))){
const err6 = {instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err6];
}
else {
vErrors.push(err6);
}
errors++;
}
}
if(data.advance_state !== undefined){
let data0 = data.advance_state;
if(typeof data0 !== "string"){
const err7 = {instancePath:instancePath+"/advance_state",schemaPath:"#/$defs/SourceAdvanceState/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err7];
}
else {
vErrors.push(err7);
}
errors++;
}
if(!((data0 === "advanced") || (data0 === "not_advanced"))){
const err8 = {instancePath:instancePath+"/advance_state",schemaPath:"#/$defs/SourceAdvanceState/enum",keyword:"enum",params:{allowedValues: schema33.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err8];
}
else {
vErrors.push(err8);
}
errors++;
}
}
if(data.batch_audit_id !== undefined){
let data1 = data.batch_audit_id;
if(typeof data1 === "string"){
if(!(formats2.test(data1))){
const err9 = {instancePath:instancePath+"/batch_audit_id",schemaPath:"#/properties/batch_audit_id/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err9];
}
else {
vErrors.push(err9);
}
errors++;
}
}
else {
const err10 = {instancePath:instancePath+"/batch_audit_id",schemaPath:"#/properties/batch_audit_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err10];
}
else {
vErrors.push(err10);
}
errors++;
}
}
if(data.business_watermark !== undefined){
let data2 = data.business_watermark;
const _errs9 = errors;
let valid2 = false;
const _errs10 = errors;
if(typeof data2 === "string"){
if(!(formats0(data2))){
const err11 = {instancePath:instancePath+"/business_watermark",schemaPath:"#/properties/business_watermark/anyOf/0/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err11];
}
else {
vErrors.push(err11);
}
errors++;
}
}
else {
const err12 = {instancePath:instancePath+"/business_watermark",schemaPath:"#/properties/business_watermark/anyOf/0/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err12];
}
else {
vErrors.push(err12);
}
errors++;
}
var _valid0 = _errs10 === errors;
valid2 = valid2 || _valid0;
const _errs12 = errors;
if(data2 !== null){
const err13 = {instancePath:instancePath+"/business_watermark",schemaPath:"#/properties/business_watermark/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err13];
}
else {
vErrors.push(err13);
}
errors++;
}
var _valid0 = _errs12 === errors;
valid2 = valid2 || _valid0;
if(!valid2){
const err14 = {instancePath:instancePath+"/business_watermark",schemaPath:"#/properties/business_watermark/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err14];
}
else {
vErrors.push(err14);
}
errors++;
}
else {
errors = _errs9;
if(vErrors !== null){
if(_errs9){
vErrors.length = _errs9;
}
else {
vErrors = null;
}
}
}
}
if(data.capability !== undefined){
let data3 = data.capability;
if(typeof data3 === "string"){
if(!pattern4.test(data3)){
const err15 = {instancePath:instancePath+"/capability",schemaPath:"#/properties/capability/pattern",keyword:"pattern",params:{pattern: "^[a-z][a-z0-9_.-]{1,99}$"},message:"must match pattern \""+"^[a-z][a-z0-9_.-]{1,99}$"+"\""};
if(vErrors === null){
vErrors = [err15];
}
else {
vErrors.push(err15);
}
errors++;
}
}
else {
const err16 = {instancePath:instancePath+"/capability",schemaPath:"#/properties/capability/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err16];
}
else {
vErrors.push(err16);
}
errors++;
}
}
if(data.data_mode !== undefined){
let data4 = data.data_mode;
if(typeof data4 !== "string"){
const err17 = {instancePath:instancePath+"/data_mode",schemaPath:"#/$defs/SourceDataMode/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err17];
}
else {
vErrors.push(err17);
}
errors++;
}
if(!((data4 === "real") || (data4 === "synthetic_development_only"))){
const err18 = {instancePath:instancePath+"/data_mode",schemaPath:"#/$defs/SourceDataMode/enum",keyword:"enum",params:{allowedValues: schema34.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err18];
}
else {
vErrors.push(err18);
}
errors++;
}
}
if(data.provenance !== undefined){
let data5 = data.provenance;
const visitedNodes0 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate22) || new Set();
if(!visitedNodes0.has(data5)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate22, visitedNodes0);
const dataNode = data5;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes0.add(dataNode);
if(!(validate22(data5, {instancePath:instancePath+"/provenance",parentData:data,parentDataProperty:"provenance",rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate22.errors : vErrors.concat(validate22.errors);
errors = vErrors.length;
}
visitedNodes0.delete(dataNode);
}
}
if(data.source_id !== undefined){
let data6 = data.source_id;
if(typeof data6 === "string"){
if(func3(data6) > 100){
const err19 = {instancePath:instancePath+"/source_id",schemaPath:"#/properties/source_id/maxLength",keyword:"maxLength",params:{limit: 100},message:"must NOT have more than 100 characters"};
if(vErrors === null){
vErrors = [err19];
}
else {
vErrors.push(err19);
}
errors++;
}
if(func3(data6) < 1){
const err20 = {instancePath:instancePath+"/source_id",schemaPath:"#/properties/source_id/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err20];
}
else {
vErrors.push(err20);
}
errors++;
}
}
else {
const err21 = {instancePath:instancePath+"/source_id",schemaPath:"#/properties/source_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err21];
}
else {
vErrors.push(err21);
}
errors++;
}
}
}
else {
const err22 = {instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err22];
}
else {
vErrors.push(err22);
}
errors++;
}
validate21.errors = vErrors;
return errors === 0;
}
validate21.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

const schema45 = {"additionalProperties":false,"description":"Exactly the five R1 coverage blocks, each present once.","properties":{"company_industry_news":{"$ref":"#/$defs/PremarketBriefingSection"},"financial_changes":{"$ref":"#/$defs/PremarketBriefingSection"},"macro_flashes":{"$ref":"#/$defs/PremarketBriefingSection"},"market_snapshot":{"$ref":"#/$defs/PremarketBriefingSection"},"statutory_announcements":{"$ref":"#/$defs/PremarketBriefingSection"}},"required":["statutory_announcements","financial_changes","market_snapshot","company_industry_news","macro_flashes"],"title":"PremarketBriefingSections","type":"object"};
const schema46 = {"additionalProperties":false,"description":"One fixed briefing coverage block.","properties":{"items":{"items":{"$ref":"#/$defs/PremarketBriefingItem"},"maxItems":100,"title":"Items","type":"array"}},"title":"PremarketBriefingSection","type":"object"};
const schema47 = {"additionalProperties":false,"description":"One evidence-linked public briefing item.","properties":{"change_type":{"$ref":"#/$defs/BriefingChangeType"},"event_id":{"format":"uuid","title":"Event Id","type":"string"},"event_revision":{"minimum":1,"title":"Event Revision","type":"integer"},"evidence_ids":{"items":{"format":"uuid","type":"string"},"maxItems":100,"minItems":1,"title":"Evidence Ids","type":"array"},"fact_summary":{"maxLength":2000,"minLength":1,"title":"Fact Summary","type":"string"},"first_seen_at":{"format":"date-time","title":"First Seen At","type":"string"},"headline":{"maxLength":300,"minLength":1,"title":"Headline","type":"string"},"inference":{"anyOf":[{"maxLength":2000,"type":"string"},{"type":"null"}],"default":null,"title":"Inference"},"published_at":{"format":"date-time","title":"Published At","type":"string"},"source_roles":{"items":{"$ref":"#/$defs/SourceRole"},"maxItems":4,"minItems":1,"title":"Source Roles","type":"array"}},"required":["event_id","event_revision","headline","fact_summary","change_type","published_at","first_seen_at","source_roles","evidence_ids"],"title":"PremarketBriefingItem","type":"object","x-runtime-invariants":["first_seen_at is >= published_at","source_roles entries are unique","evidence_ids entries are unique"]};
const schema48 = {"enum":["new","update","correction","contradiction"],"title":"BriefingChangeType","type":"string"};
const schema49 = {"description":"Permitted public source roles.","enum":["statutory","professional_market","professional_information","discovery"],"title":"SourceRole","type":"string"};

function validate27(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}, isAllOfVariant = 0}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate27.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.event_id === undefined){
const err0 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "event_id"},message:"must have required property '"+"event_id"+"'"};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
if(data.event_revision === undefined){
const err1 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "event_revision"},message:"must have required property '"+"event_revision"+"'"};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
if(data.headline === undefined){
const err2 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "headline"},message:"must have required property '"+"headline"+"'"};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
if(data.fact_summary === undefined){
const err3 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "fact_summary"},message:"must have required property '"+"fact_summary"+"'"};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
}
if(data.change_type === undefined){
const err4 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "change_type"},message:"must have required property '"+"change_type"+"'"};
if(vErrors === null){
vErrors = [err4];
}
else {
vErrors.push(err4);
}
errors++;
}
if(data.published_at === undefined){
const err5 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "published_at"},message:"must have required property '"+"published_at"+"'"};
if(vErrors === null){
vErrors = [err5];
}
else {
vErrors.push(err5);
}
errors++;
}
if(data.first_seen_at === undefined){
const err6 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "first_seen_at"},message:"must have required property '"+"first_seen_at"+"'"};
if(vErrors === null){
vErrors = [err6];
}
else {
vErrors.push(err6);
}
errors++;
}
if(data.source_roles === undefined){
const err7 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "source_roles"},message:"must have required property '"+"source_roles"+"'"};
if(vErrors === null){
vErrors = [err7];
}
else {
vErrors.push(err7);
}
errors++;
}
if(data.evidence_ids === undefined){
const err8 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "evidence_ids"},message:"must have required property '"+"evidence_ids"+"'"};
if(vErrors === null){
vErrors = [err8];
}
else {
vErrors.push(err8);
}
errors++;
}
for(const key0 in data){
if(!(func1.call(schema47.properties, key0))){
const err9 = {instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err9];
}
else {
vErrors.push(err9);
}
errors++;
}
}
if(data.change_type !== undefined){
let data0 = data.change_type;
if(typeof data0 !== "string"){
const err10 = {instancePath:instancePath+"/change_type",schemaPath:"#/$defs/BriefingChangeType/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err10];
}
else {
vErrors.push(err10);
}
errors++;
}
if(!((((data0 === "new") || (data0 === "update")) || (data0 === "correction")) || (data0 === "contradiction"))){
const err11 = {instancePath:instancePath+"/change_type",schemaPath:"#/$defs/BriefingChangeType/enum",keyword:"enum",params:{allowedValues: schema48.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err11];
}
else {
vErrors.push(err11);
}
errors++;
}
}
if(data.event_id !== undefined){
let data1 = data.event_id;
if(typeof data1 === "string"){
if(!(formats2.test(data1))){
const err12 = {instancePath:instancePath+"/event_id",schemaPath:"#/properties/event_id/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err12];
}
else {
vErrors.push(err12);
}
errors++;
}
}
else {
const err13 = {instancePath:instancePath+"/event_id",schemaPath:"#/properties/event_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err13];
}
else {
vErrors.push(err13);
}
errors++;
}
}
if(data.event_revision !== undefined){
let data2 = data.event_revision;
if(!(((typeof data2 == "number") && (!(data2 % 1) && !isNaN(data2))) && (isFinite(data2)))){
const err14 = {instancePath:instancePath+"/event_revision",schemaPath:"#/properties/event_revision/type",keyword:"type",params:{type: "integer"},message:"must be integer"};
if(vErrors === null){
vErrors = [err14];
}
else {
vErrors.push(err14);
}
errors++;
}
if((typeof data2 == "number") && (isFinite(data2))){
if(data2 < 1 || isNaN(data2)){
const err15 = {instancePath:instancePath+"/event_revision",schemaPath:"#/properties/event_revision/minimum",keyword:"minimum",params:{comparison: ">=", limit: 1},message:"must be >= 1"};
if(vErrors === null){
vErrors = [err15];
}
else {
vErrors.push(err15);
}
errors++;
}
}
}
if(data.evidence_ids !== undefined){
let data3 = data.evidence_ids;
if(Array.isArray(data3)){
if(data3.length > 100){
const err16 = {instancePath:instancePath+"/evidence_ids",schemaPath:"#/properties/evidence_ids/maxItems",keyword:"maxItems",params:{limit: 100},message:"must NOT have more than 100 items"};
if(vErrors === null){
vErrors = [err16];
}
else {
vErrors.push(err16);
}
errors++;
}
if(data3.length < 1){
const err17 = {instancePath:instancePath+"/evidence_ids",schemaPath:"#/properties/evidence_ids/minItems",keyword:"minItems",params:{limit: 1},message:"must NOT have fewer than 1 items"};
if(vErrors === null){
vErrors = [err17];
}
else {
vErrors.push(err17);
}
errors++;
}
const len0 = data3.length;
for(let i0=0; i0<len0; i0++){
let data4 = data3[i0];
if(typeof data4 === "string"){
if(!(formats2.test(data4))){
const err18 = {instancePath:instancePath+"/evidence_ids/" + i0,schemaPath:"#/properties/evidence_ids/items/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err18];
}
else {
vErrors.push(err18);
}
errors++;
}
}
else {
const err19 = {instancePath:instancePath+"/evidence_ids/" + i0,schemaPath:"#/properties/evidence_ids/items/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err19];
}
else {
vErrors.push(err19);
}
errors++;
}
}
}
else {
const err20 = {instancePath:instancePath+"/evidence_ids",schemaPath:"#/properties/evidence_ids/type",keyword:"type",params:{type: "array"},message:"must be array"};
if(vErrors === null){
vErrors = [err20];
}
else {
vErrors.push(err20);
}
errors++;
}
}
if(data.fact_summary !== undefined){
let data5 = data.fact_summary;
if(typeof data5 === "string"){
if(func3(data5) > 2000){
const err21 = {instancePath:instancePath+"/fact_summary",schemaPath:"#/properties/fact_summary/maxLength",keyword:"maxLength",params:{limit: 2000},message:"must NOT have more than 2000 characters"};
if(vErrors === null){
vErrors = [err21];
}
else {
vErrors.push(err21);
}
errors++;
}
if(func3(data5) < 1){
const err22 = {instancePath:instancePath+"/fact_summary",schemaPath:"#/properties/fact_summary/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err22];
}
else {
vErrors.push(err22);
}
errors++;
}
}
else {
const err23 = {instancePath:instancePath+"/fact_summary",schemaPath:"#/properties/fact_summary/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err23];
}
else {
vErrors.push(err23);
}
errors++;
}
}
if(data.first_seen_at !== undefined){
let data6 = data.first_seen_at;
if(typeof data6 === "string"){
if(!(formats0(data6))){
const err24 = {instancePath:instancePath+"/first_seen_at",schemaPath:"#/properties/first_seen_at/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err24];
}
else {
vErrors.push(err24);
}
errors++;
}
}
else {
const err25 = {instancePath:instancePath+"/first_seen_at",schemaPath:"#/properties/first_seen_at/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err25];
}
else {
vErrors.push(err25);
}
errors++;
}
}
if(data.headline !== undefined){
let data7 = data.headline;
if(typeof data7 === "string"){
if(func3(data7) > 300){
const err26 = {instancePath:instancePath+"/headline",schemaPath:"#/properties/headline/maxLength",keyword:"maxLength",params:{limit: 300},message:"must NOT have more than 300 characters"};
if(vErrors === null){
vErrors = [err26];
}
else {
vErrors.push(err26);
}
errors++;
}
if(func3(data7) < 1){
const err27 = {instancePath:instancePath+"/headline",schemaPath:"#/properties/headline/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err27];
}
else {
vErrors.push(err27);
}
errors++;
}
}
else {
const err28 = {instancePath:instancePath+"/headline",schemaPath:"#/properties/headline/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err28];
}
else {
vErrors.push(err28);
}
errors++;
}
}
if(data.inference !== undefined){
let data8 = data.inference;
const _errs21 = errors;
let valid4 = false;
const _errs22 = errors;
if(typeof data8 === "string"){
if(func3(data8) > 2000){
const err29 = {instancePath:instancePath+"/inference",schemaPath:"#/properties/inference/anyOf/0/maxLength",keyword:"maxLength",params:{limit: 2000},message:"must NOT have more than 2000 characters"};
if(vErrors === null){
vErrors = [err29];
}
else {
vErrors.push(err29);
}
errors++;
}
}
else {
const err30 = {instancePath:instancePath+"/inference",schemaPath:"#/properties/inference/anyOf/0/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err30];
}
else {
vErrors.push(err30);
}
errors++;
}
var _valid0 = _errs22 === errors;
valid4 = valid4 || _valid0;
const _errs24 = errors;
if(data8 !== null){
const err31 = {instancePath:instancePath+"/inference",schemaPath:"#/properties/inference/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err31];
}
else {
vErrors.push(err31);
}
errors++;
}
var _valid0 = _errs24 === errors;
valid4 = valid4 || _valid0;
if(!valid4){
const err32 = {instancePath:instancePath+"/inference",schemaPath:"#/properties/inference/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err32];
}
else {
vErrors.push(err32);
}
errors++;
}
else {
errors = _errs21;
if(vErrors !== null){
if(_errs21){
vErrors.length = _errs21;
}
else {
vErrors = null;
}
}
}
}
if(data.published_at !== undefined){
let data9 = data.published_at;
if(typeof data9 === "string"){
if(!(formats0(data9))){
const err33 = {instancePath:instancePath+"/published_at",schemaPath:"#/properties/published_at/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err33];
}
else {
vErrors.push(err33);
}
errors++;
}
}
else {
const err34 = {instancePath:instancePath+"/published_at",schemaPath:"#/properties/published_at/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err34];
}
else {
vErrors.push(err34);
}
errors++;
}
}
if(data.source_roles !== undefined){
let data10 = data.source_roles;
if(Array.isArray(data10)){
if(data10.length > 4){
const err35 = {instancePath:instancePath+"/source_roles",schemaPath:"#/properties/source_roles/maxItems",keyword:"maxItems",params:{limit: 4},message:"must NOT have more than 4 items"};
if(vErrors === null){
vErrors = [err35];
}
else {
vErrors.push(err35);
}
errors++;
}
if(data10.length < 1){
const err36 = {instancePath:instancePath+"/source_roles",schemaPath:"#/properties/source_roles/minItems",keyword:"minItems",params:{limit: 1},message:"must NOT have fewer than 1 items"};
if(vErrors === null){
vErrors = [err36];
}
else {
vErrors.push(err36);
}
errors++;
}
const len1 = data10.length;
for(let i1=0; i1<len1; i1++){
let data11 = data10[i1];
if(typeof data11 !== "string"){
const err37 = {instancePath:instancePath+"/source_roles/" + i1,schemaPath:"#/$defs/SourceRole/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err37];
}
else {
vErrors.push(err37);
}
errors++;
}
if(!((((data11 === "statutory") || (data11 === "professional_market")) || (data11 === "professional_information")) || (data11 === "discovery"))){
const err38 = {instancePath:instancePath+"/source_roles/" + i1,schemaPath:"#/$defs/SourceRole/enum",keyword:"enum",params:{allowedValues: schema49.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err38];
}
else {
vErrors.push(err38);
}
errors++;
}
}
}
else {
const err39 = {instancePath:instancePath+"/source_roles",schemaPath:"#/properties/source_roles/type",keyword:"type",params:{type: "array"},message:"must be array"};
if(vErrors === null){
vErrors = [err39];
}
else {
vErrors.push(err39);
}
errors++;
}
}
}
else {
const err40 = {instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err40];
}
else {
vErrors.push(err40);
}
errors++;
}
validate27.errors = vErrors;
return errors === 0;
}
validate27.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};


function validate26(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}, isAllOfVariant = 0}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate26.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(data && typeof data == "object" && !Array.isArray(data)){
for(const key0 in data){
if(!(key0 === "items")){
const err0 = {instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
}
if(data.items !== undefined){
let data0 = data.items;
if(Array.isArray(data0)){
if(data0.length > 100){
const err1 = {instancePath:instancePath+"/items",schemaPath:"#/properties/items/maxItems",keyword:"maxItems",params:{limit: 100},message:"must NOT have more than 100 items"};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
const len0 = data0.length;
for(let i0=0; i0<len0; i0++){
let data1 = data0[i0];
const visitedNodes0 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate27) || new Set();
if(!visitedNodes0.has(data1)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate27, visitedNodes0);
const dataNode = data1;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes0.add(dataNode);
if(!(validate27(data1, {instancePath:instancePath+"/items/" + i0,parentData:data0,parentDataProperty:i0,rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate27.errors : vErrors.concat(validate27.errors);
errors = vErrors.length;
}
visitedNodes0.delete(dataNode);
}
}
}
else {
const err2 = {instancePath:instancePath+"/items",schemaPath:"#/properties/items/type",keyword:"type",params:{type: "array"},message:"must be array"};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
}
}
else {
const err3 = {instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
}
validate26.errors = vErrors;
return errors === 0;
}
validate26.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};


function validate25(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}, isAllOfVariant = 0}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate25.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.statutory_announcements === undefined){
const err0 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "statutory_announcements"},message:"must have required property '"+"statutory_announcements"+"'"};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
if(data.financial_changes === undefined){
const err1 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "financial_changes"},message:"must have required property '"+"financial_changes"+"'"};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
if(data.market_snapshot === undefined){
const err2 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "market_snapshot"},message:"must have required property '"+"market_snapshot"+"'"};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
if(data.company_industry_news === undefined){
const err3 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "company_industry_news"},message:"must have required property '"+"company_industry_news"+"'"};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
}
if(data.macro_flashes === undefined){
const err4 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "macro_flashes"},message:"must have required property '"+"macro_flashes"+"'"};
if(vErrors === null){
vErrors = [err4];
}
else {
vErrors.push(err4);
}
errors++;
}
for(const key0 in data){
if(!(((((key0 === "company_industry_news") || (key0 === "financial_changes")) || (key0 === "macro_flashes")) || (key0 === "market_snapshot")) || (key0 === "statutory_announcements"))){
const err5 = {instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err5];
}
else {
vErrors.push(err5);
}
errors++;
}
}
if(data.company_industry_news !== undefined){
let data0 = data.company_industry_news;
const visitedNodes0 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate26) || new Set();
if(!visitedNodes0.has(data0)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate26, visitedNodes0);
const dataNode = data0;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes0.add(dataNode);
if(!(validate26(data0, {instancePath:instancePath+"/company_industry_news",parentData:data,parentDataProperty:"company_industry_news",rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate26.errors : vErrors.concat(validate26.errors);
errors = vErrors.length;
}
visitedNodes0.delete(dataNode);
}
}
if(data.financial_changes !== undefined){
let data1 = data.financial_changes;
const visitedNodes1 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate26) || new Set();
if(!visitedNodes1.has(data1)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate26, visitedNodes1);
const dataNode = data1;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes1.add(dataNode);
if(!(validate26(data1, {instancePath:instancePath+"/financial_changes",parentData:data,parentDataProperty:"financial_changes",rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate26.errors : vErrors.concat(validate26.errors);
errors = vErrors.length;
}
visitedNodes1.delete(dataNode);
}
}
if(data.macro_flashes !== undefined){
let data2 = data.macro_flashes;
const visitedNodes2 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate26) || new Set();
if(!visitedNodes2.has(data2)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate26, visitedNodes2);
const dataNode = data2;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes2.add(dataNode);
if(!(validate26(data2, {instancePath:instancePath+"/macro_flashes",parentData:data,parentDataProperty:"macro_flashes",rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate26.errors : vErrors.concat(validate26.errors);
errors = vErrors.length;
}
visitedNodes2.delete(dataNode);
}
}
if(data.market_snapshot !== undefined){
let data3 = data.market_snapshot;
const visitedNodes3 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate26) || new Set();
if(!visitedNodes3.has(data3)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate26, visitedNodes3);
const dataNode = data3;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes3.add(dataNode);
if(!(validate26(data3, {instancePath:instancePath+"/market_snapshot",parentData:data,parentDataProperty:"market_snapshot",rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate26.errors : vErrors.concat(validate26.errors);
errors = vErrors.length;
}
visitedNodes3.delete(dataNode);
}
}
if(data.statutory_announcements !== undefined){
let data4 = data.statutory_announcements;
const visitedNodes4 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate26) || new Set();
if(!visitedNodes4.has(data4)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate26, visitedNodes4);
const dataNode = data4;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes4.add(dataNode);
if(!(validate26(data4, {instancePath:instancePath+"/statutory_announcements",parentData:data,parentDataProperty:"statutory_announcements",rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate26.errors : vErrors.concat(validate26.errors);
errors = vErrors.length;
}
visitedNodes4.delete(dataNode);
}
}
}
else {
const err6 = {instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err6];
}
else {
vErrors.push(err6);
}
errors++;
}
validate25.errors = vErrors;
return errors === 0;
}
validate25.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

const schema50 = {"additionalProperties":false,"description":"Source capability coverage carrying complete R1 1.2 lineage.","properties":{"capability":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Capability","type":"string"},"gap_reason":{"anyOf":[{"maxLength":500,"minLength":1,"type":"string"},{"type":"null"}],"default":null,"title":"Gap Reason"},"last_success_at":{"anyOf":[{"format":"date-time","type":"string"},{"type":"null"}],"default":null,"title":"Last Success At"},"license_id":{"maxLength":100,"minLength":1,"title":"License Id","type":"string"},"observed_at":{"format":"date-time","title":"Observed At","type":"string"},"progress":{"$ref":"#/$defs/SourceIngestionProgress"},"provenance":{"$ref":"#/$defs/SourceProvenanceV1_2"},"source_id":{"maxLength":100,"minLength":1,"title":"Source Id","type":"string"},"source_role":{"$ref":"#/$defs/SourceRole"},"status":{"$ref":"#/$defs/SourceCoverageStatus"}},"required":["source_id","capability","source_role","status","observed_at","license_id","progress","provenance"],"title":"SourceCoverageV1_2","type":"object","x-runtime-invariants":["last_success_at is <= observed_at when present","status=ok requires last_success_at","status other than ok requires gap_reason","progress.business_watermark is <= observed_at when present","progress.advance_state=advanced requires last_success_at"]};
const schema55 = {"description":"Public source capability coverage states.","enum":["ok","delayed","unavailable","rights_blocked","not_qualified","timeliness_unverified"],"title":"SourceCoverageStatus","type":"string"};
const schema51 = {"additionalProperties":false,"description":"Public provider-neutral ingestion progress metadata.","properties":{"advance_state":{"$ref":"#/$defs/SourceAdvanceState"},"batch_audit_id":{"format":"uuid","title":"Batch Audit Id","type":"string"},"business_watermark":{"anyOf":[{"format":"date-time","type":"string"},{"type":"null"}],"default":null,"title":"Business Watermark"},"data_mode":{"$ref":"#/$defs/SourceDataMode"}},"required":["batch_audit_id","advance_state","data_mode"],"title":"SourceIngestionProgress","type":"object","x-runtime-invariants":["advance_state=advanced requires business_watermark"]};

function validate36(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}, isAllOfVariant = 0}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate36.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.batch_audit_id === undefined){
const err0 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "batch_audit_id"},message:"must have required property '"+"batch_audit_id"+"'"};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
if(data.advance_state === undefined){
const err1 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "advance_state"},message:"must have required property '"+"advance_state"+"'"};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
if(data.data_mode === undefined){
const err2 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "data_mode"},message:"must have required property '"+"data_mode"+"'"};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
for(const key0 in data){
if(!((((key0 === "advance_state") || (key0 === "batch_audit_id")) || (key0 === "business_watermark")) || (key0 === "data_mode"))){
const err3 = {instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
}
}
if(data.advance_state !== undefined){
let data0 = data.advance_state;
if(typeof data0 !== "string"){
const err4 = {instancePath:instancePath+"/advance_state",schemaPath:"#/$defs/SourceAdvanceState/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err4];
}
else {
vErrors.push(err4);
}
errors++;
}
if(!((data0 === "advanced") || (data0 === "not_advanced"))){
const err5 = {instancePath:instancePath+"/advance_state",schemaPath:"#/$defs/SourceAdvanceState/enum",keyword:"enum",params:{allowedValues: schema33.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err5];
}
else {
vErrors.push(err5);
}
errors++;
}
}
if(data.batch_audit_id !== undefined){
let data1 = data.batch_audit_id;
if(typeof data1 === "string"){
if(!(formats2.test(data1))){
const err6 = {instancePath:instancePath+"/batch_audit_id",schemaPath:"#/properties/batch_audit_id/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err6];
}
else {
vErrors.push(err6);
}
errors++;
}
}
else {
const err7 = {instancePath:instancePath+"/batch_audit_id",schemaPath:"#/properties/batch_audit_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err7];
}
else {
vErrors.push(err7);
}
errors++;
}
}
if(data.business_watermark !== undefined){
let data2 = data.business_watermark;
const _errs9 = errors;
let valid2 = false;
const _errs10 = errors;
if(typeof data2 === "string"){
if(!(formats0(data2))){
const err8 = {instancePath:instancePath+"/business_watermark",schemaPath:"#/properties/business_watermark/anyOf/0/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err8];
}
else {
vErrors.push(err8);
}
errors++;
}
}
else {
const err9 = {instancePath:instancePath+"/business_watermark",schemaPath:"#/properties/business_watermark/anyOf/0/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err9];
}
else {
vErrors.push(err9);
}
errors++;
}
var _valid0 = _errs10 === errors;
valid2 = valid2 || _valid0;
const _errs12 = errors;
if(data2 !== null){
const err10 = {instancePath:instancePath+"/business_watermark",schemaPath:"#/properties/business_watermark/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err10];
}
else {
vErrors.push(err10);
}
errors++;
}
var _valid0 = _errs12 === errors;
valid2 = valid2 || _valid0;
if(!valid2){
const err11 = {instancePath:instancePath+"/business_watermark",schemaPath:"#/properties/business_watermark/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err11];
}
else {
vErrors.push(err11);
}
errors++;
}
else {
errors = _errs9;
if(vErrors !== null){
if(_errs9){
vErrors.length = _errs9;
}
else {
vErrors = null;
}
}
}
}
if(data.data_mode !== undefined){
let data3 = data.data_mode;
if(typeof data3 !== "string"){
const err12 = {instancePath:instancePath+"/data_mode",schemaPath:"#/$defs/SourceDataMode/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err12];
}
else {
vErrors.push(err12);
}
errors++;
}
if(!((data3 === "real") || (data3 === "synthetic_development_only"))){
const err13 = {instancePath:instancePath+"/data_mode",schemaPath:"#/$defs/SourceDataMode/enum",keyword:"enum",params:{allowedValues: schema34.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err13];
}
else {
vErrors.push(err13);
}
errors++;
}
}
}
else {
const err14 = {instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err14];
}
else {
vErrors.push(err14);
}
errors++;
}
validate36.errors = vErrors;
return errors === 0;
}
validate36.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};


function validate35(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}, isAllOfVariant = 0}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate35.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.source_id === undefined){
const err0 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "source_id"},message:"must have required property '"+"source_id"+"'"};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
if(data.capability === undefined){
const err1 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "capability"},message:"must have required property '"+"capability"+"'"};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
if(data.source_role === undefined){
const err2 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "source_role"},message:"must have required property '"+"source_role"+"'"};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
if(data.status === undefined){
const err3 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "status"},message:"must have required property '"+"status"+"'"};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
}
if(data.observed_at === undefined){
const err4 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "observed_at"},message:"must have required property '"+"observed_at"+"'"};
if(vErrors === null){
vErrors = [err4];
}
else {
vErrors.push(err4);
}
errors++;
}
if(data.license_id === undefined){
const err5 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "license_id"},message:"must have required property '"+"license_id"+"'"};
if(vErrors === null){
vErrors = [err5];
}
else {
vErrors.push(err5);
}
errors++;
}
if(data.progress === undefined){
const err6 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "progress"},message:"must have required property '"+"progress"+"'"};
if(vErrors === null){
vErrors = [err6];
}
else {
vErrors.push(err6);
}
errors++;
}
if(data.provenance === undefined){
const err7 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "provenance"},message:"must have required property '"+"provenance"+"'"};
if(vErrors === null){
vErrors = [err7];
}
else {
vErrors.push(err7);
}
errors++;
}
for(const key0 in data){
if(!(func1.call(schema50.properties, key0))){
const err8 = {instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err8];
}
else {
vErrors.push(err8);
}
errors++;
}
}
if(data.capability !== undefined){
let data0 = data.capability;
if(typeof data0 === "string"){
if(!pattern4.test(data0)){
const err9 = {instancePath:instancePath+"/capability",schemaPath:"#/properties/capability/pattern",keyword:"pattern",params:{pattern: "^[a-z][a-z0-9_.-]{1,99}$"},message:"must match pattern \""+"^[a-z][a-z0-9_.-]{1,99}$"+"\""};
if(vErrors === null){
vErrors = [err9];
}
else {
vErrors.push(err9);
}
errors++;
}
}
else {
const err10 = {instancePath:instancePath+"/capability",schemaPath:"#/properties/capability/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err10];
}
else {
vErrors.push(err10);
}
errors++;
}
}
if(data.gap_reason !== undefined){
let data1 = data.gap_reason;
const _errs6 = errors;
let valid1 = false;
const _errs7 = errors;
if(typeof data1 === "string"){
if(func3(data1) > 500){
const err11 = {instancePath:instancePath+"/gap_reason",schemaPath:"#/properties/gap_reason/anyOf/0/maxLength",keyword:"maxLength",params:{limit: 500},message:"must NOT have more than 500 characters"};
if(vErrors === null){
vErrors = [err11];
}
else {
vErrors.push(err11);
}
errors++;
}
if(func3(data1) < 1){
const err12 = {instancePath:instancePath+"/gap_reason",schemaPath:"#/properties/gap_reason/anyOf/0/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err12];
}
else {
vErrors.push(err12);
}
errors++;
}
}
else {
const err13 = {instancePath:instancePath+"/gap_reason",schemaPath:"#/properties/gap_reason/anyOf/0/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err13];
}
else {
vErrors.push(err13);
}
errors++;
}
var _valid0 = _errs7 === errors;
valid1 = valid1 || _valid0;
const _errs9 = errors;
if(data1 !== null){
const err14 = {instancePath:instancePath+"/gap_reason",schemaPath:"#/properties/gap_reason/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err14];
}
else {
vErrors.push(err14);
}
errors++;
}
var _valid0 = _errs9 === errors;
valid1 = valid1 || _valid0;
if(!valid1){
const err15 = {instancePath:instancePath+"/gap_reason",schemaPath:"#/properties/gap_reason/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err15];
}
else {
vErrors.push(err15);
}
errors++;
}
else {
errors = _errs6;
if(vErrors !== null){
if(_errs6){
vErrors.length = _errs6;
}
else {
vErrors = null;
}
}
}
}
if(data.last_success_at !== undefined){
let data2 = data.last_success_at;
const _errs12 = errors;
let valid2 = false;
const _errs13 = errors;
if(typeof data2 === "string"){
if(!(formats0(data2))){
const err16 = {instancePath:instancePath+"/last_success_at",schemaPath:"#/properties/last_success_at/anyOf/0/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err16];
}
else {
vErrors.push(err16);
}
errors++;
}
}
else {
const err17 = {instancePath:instancePath+"/last_success_at",schemaPath:"#/properties/last_success_at/anyOf/0/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err17];
}
else {
vErrors.push(err17);
}
errors++;
}
var _valid1 = _errs13 === errors;
valid2 = valid2 || _valid1;
const _errs15 = errors;
if(data2 !== null){
const err18 = {instancePath:instancePath+"/last_success_at",schemaPath:"#/properties/last_success_at/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err18];
}
else {
vErrors.push(err18);
}
errors++;
}
var _valid1 = _errs15 === errors;
valid2 = valid2 || _valid1;
if(!valid2){
const err19 = {instancePath:instancePath+"/last_success_at",schemaPath:"#/properties/last_success_at/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err19];
}
else {
vErrors.push(err19);
}
errors++;
}
else {
errors = _errs12;
if(vErrors !== null){
if(_errs12){
vErrors.length = _errs12;
}
else {
vErrors = null;
}
}
}
}
if(data.license_id !== undefined){
let data3 = data.license_id;
if(typeof data3 === "string"){
if(func3(data3) > 100){
const err20 = {instancePath:instancePath+"/license_id",schemaPath:"#/properties/license_id/maxLength",keyword:"maxLength",params:{limit: 100},message:"must NOT have more than 100 characters"};
if(vErrors === null){
vErrors = [err20];
}
else {
vErrors.push(err20);
}
errors++;
}
if(func3(data3) < 1){
const err21 = {instancePath:instancePath+"/license_id",schemaPath:"#/properties/license_id/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err21];
}
else {
vErrors.push(err21);
}
errors++;
}
}
else {
const err22 = {instancePath:instancePath+"/license_id",schemaPath:"#/properties/license_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err22];
}
else {
vErrors.push(err22);
}
errors++;
}
}
if(data.observed_at !== undefined){
let data4 = data.observed_at;
if(typeof data4 === "string"){
if(!(formats0(data4))){
const err23 = {instancePath:instancePath+"/observed_at",schemaPath:"#/properties/observed_at/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err23];
}
else {
vErrors.push(err23);
}
errors++;
}
}
else {
const err24 = {instancePath:instancePath+"/observed_at",schemaPath:"#/properties/observed_at/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err24];
}
else {
vErrors.push(err24);
}
errors++;
}
}
if(data.progress !== undefined){
let data5 = data.progress;
const visitedNodes0 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate36) || new Set();
if(!visitedNodes0.has(data5)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate36, visitedNodes0);
const dataNode = data5;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes0.add(dataNode);
if(!(validate36(data5, {instancePath:instancePath+"/progress",parentData:data,parentDataProperty:"progress",rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate36.errors : vErrors.concat(validate36.errors);
errors = vErrors.length;
}
visitedNodes0.delete(dataNode);
}
}
if(data.provenance !== undefined){
let data6 = data.provenance;
const visitedNodes1 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate22) || new Set();
if(!visitedNodes1.has(data6)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate22, visitedNodes1);
const dataNode = data6;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes1.add(dataNode);
if(!(validate22(data6, {instancePath:instancePath+"/provenance",parentData:data,parentDataProperty:"provenance",rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate22.errors : vErrors.concat(validate22.errors);
errors = vErrors.length;
}
visitedNodes1.delete(dataNode);
}
}
if(data.source_id !== undefined){
let data7 = data.source_id;
if(typeof data7 === "string"){
if(func3(data7) > 100){
const err25 = {instancePath:instancePath+"/source_id",schemaPath:"#/properties/source_id/maxLength",keyword:"maxLength",params:{limit: 100},message:"must NOT have more than 100 characters"};
if(vErrors === null){
vErrors = [err25];
}
else {
vErrors.push(err25);
}
errors++;
}
if(func3(data7) < 1){
const err26 = {instancePath:instancePath+"/source_id",schemaPath:"#/properties/source_id/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err26];
}
else {
vErrors.push(err26);
}
errors++;
}
}
else {
const err27 = {instancePath:instancePath+"/source_id",schemaPath:"#/properties/source_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err27];
}
else {
vErrors.push(err27);
}
errors++;
}
}
if(data.source_role !== undefined){
let data8 = data.source_role;
if(typeof data8 !== "string"){
const err28 = {instancePath:instancePath+"/source_role",schemaPath:"#/$defs/SourceRole/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err28];
}
else {
vErrors.push(err28);
}
errors++;
}
if(!((((data8 === "statutory") || (data8 === "professional_market")) || (data8 === "professional_information")) || (data8 === "discovery"))){
const err29 = {instancePath:instancePath+"/source_role",schemaPath:"#/$defs/SourceRole/enum",keyword:"enum",params:{allowedValues: schema49.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err29];
}
else {
vErrors.push(err29);
}
errors++;
}
}
if(data.status !== undefined){
let data9 = data.status;
if(typeof data9 !== "string"){
const err30 = {instancePath:instancePath+"/status",schemaPath:"#/$defs/SourceCoverageStatus/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err30];
}
else {
vErrors.push(err30);
}
errors++;
}
if(!((((((data9 === "ok") || (data9 === "delayed")) || (data9 === "unavailable")) || (data9 === "rights_blocked")) || (data9 === "not_qualified")) || (data9 === "timeliness_unverified"))){
const err31 = {instancePath:instancePath+"/status",schemaPath:"#/$defs/SourceCoverageStatus/enum",keyword:"enum",params:{allowedValues: schema55.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err31];
}
else {
vErrors.push(err31);
}
errors++;
}
}
}
else {
const err32 = {instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err32];
}
else {
vErrors.push(err32);
}
errors++;
}
validate35.errors = vErrors;
return errors === 0;
}
validate35.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

const pattern8 = new RegExp("^[a-z][a-z0-9_]{1,31}$", "u");
const pattern9 = new RegExp("^[A-Z]{3}$", "u");
const pattern10 = new RegExp("^[A-Z0-9_]{2,32}$", "u");
const pattern11 = new RegExp("^[A-Z0-9_]+:[A-Za-z0-9._-]+$", "u");

function validate20(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}, isAllOfVariant = 0}={}){
/*# sourceURL="https://stock-market-agent.local/schemas/briefings/premarket-view.v1.2.schema.json" */;
let vErrors = null;
let errors = 0;
const evaluated0 = validate20.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.schema_version === undefined){
const err0 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "schema_version"},message:"must have required property '"+"schema_version"+"'"};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
if(data.instrument === undefined){
const err1 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "instrument"},message:"must have required property '"+"instrument"+"'"};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
if(data.comparison_since === undefined){
const err2 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "comparison_since"},message:"must have required property '"+"comparison_since"+"'"};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
if(data.as_of === undefined){
const err3 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "as_of"},message:"must have required property '"+"as_of"+"'"};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
}
if(data.generated_at === undefined){
const err4 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "generated_at"},message:"must have required property '"+"generated_at"+"'"};
if(vErrors === null){
vErrors = [err4];
}
else {
vErrors.push(err4);
}
errors++;
}
if(data.sections === undefined){
const err5 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "sections"},message:"must have required property '"+"sections"+"'"};
if(vErrors === null){
vErrors = [err5];
}
else {
vErrors.push(err5);
}
errors++;
}
if(data.ingestion_progress === undefined){
const err6 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "ingestion_progress"},message:"must have required property '"+"ingestion_progress"+"'"};
if(vErrors === null){
vErrors = [err6];
}
else {
vErrors.push(err6);
}
errors++;
}
if(data.ruleset_version === undefined){
const err7 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "ruleset_version"},message:"must have required property '"+"ruleset_version"+"'"};
if(vErrors === null){
vErrors = [err7];
}
else {
vErrors.push(err7);
}
errors++;
}
if(data.runtime_mode === undefined){
const err8 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "runtime_mode"},message:"must have required property '"+"runtime_mode"+"'"};
if(vErrors === null){
vErrors = [err8];
}
else {
vErrors.push(err8);
}
errors++;
}
if(data.release_tier === undefined){
const err9 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "release_tier"},message:"must have required property '"+"release_tier"+"'"};
if(vErrors === null){
vErrors = [err9];
}
else {
vErrors.push(err9);
}
errors++;
}
for(const key0 in data){
if(!(func1.call(schema31.properties, key0))){
const err10 = {instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err10];
}
else {
vErrors.push(err10);
}
errors++;
}
}
if(data.as_of !== undefined){
let data0 = data.as_of;
if(typeof data0 === "string"){
if(!(formats0(data0))){
const err11 = {instancePath:instancePath+"/as_of",schemaPath:"#/properties/as_of/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err11];
}
else {
vErrors.push(err11);
}
errors++;
}
}
else {
const err12 = {instancePath:instancePath+"/as_of",schemaPath:"#/properties/as_of/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err12];
}
else {
vErrors.push(err12);
}
errors++;
}
}
if(data.briefing_id !== undefined){
let data1 = data.briefing_id;
if(typeof data1 === "string"){
if(!(formats2.test(data1))){
const err13 = {instancePath:instancePath+"/briefing_id",schemaPath:"#/properties/briefing_id/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err13];
}
else {
vErrors.push(err13);
}
errors++;
}
}
else {
const err14 = {instancePath:instancePath+"/briefing_id",schemaPath:"#/properties/briefing_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err14];
}
else {
vErrors.push(err14);
}
errors++;
}
}
if(data.comparison_since !== undefined){
let data2 = data.comparison_since;
if(typeof data2 === "string"){
if(!(formats0(data2))){
const err15 = {instancePath:instancePath+"/comparison_since",schemaPath:"#/properties/comparison_since/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err15];
}
else {
vErrors.push(err15);
}
errors++;
}
}
else {
const err16 = {instancePath:instancePath+"/comparison_since",schemaPath:"#/properties/comparison_since/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err16];
}
else {
vErrors.push(err16);
}
errors++;
}
}
if(data.disclaimer !== undefined){
if(typeof data.disclaimer !== "string"){
const err17 = {instancePath:instancePath+"/disclaimer",schemaPath:"#/properties/disclaimer/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err17];
}
else {
vErrors.push(err17);
}
errors++;
}
}
if(data.generated_at !== undefined){
let data4 = data.generated_at;
if(typeof data4 === "string"){
if(!(formats0(data4))){
const err18 = {instancePath:instancePath+"/generated_at",schemaPath:"#/properties/generated_at/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err18];
}
else {
vErrors.push(err18);
}
errors++;
}
}
else {
const err19 = {instancePath:instancePath+"/generated_at",schemaPath:"#/properties/generated_at/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err19];
}
else {
vErrors.push(err19);
}
errors++;
}
}
if(data.ingestion_progress !== undefined){
let data5 = data.ingestion_progress;
const visitedNodes0 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate21) || new Set();
if(!visitedNodes0.has(data5)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate21, visitedNodes0);
const dataNode = data5;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes0.add(dataNode);
if(!(validate21(data5, {instancePath:instancePath+"/ingestion_progress",parentData:data,parentDataProperty:"ingestion_progress",rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate21.errors : vErrors.concat(validate21.errors);
errors = vErrors.length;
}
visitedNodes0.delete(dataNode);
}
}
if(data.instrument !== undefined){
let data6 = data.instrument;
if(data6 && typeof data6 == "object" && !Array.isArray(data6)){
if(data6.instrument_id === undefined){
const err20 = {instancePath:instancePath+"/instrument",schemaPath:"#/$defs/InstrumentRef/required",keyword:"required",params:{missingProperty: "instrument_id"},message:"must have required property '"+"instrument_id"+"'"};
if(vErrors === null){
vErrors = [err20];
}
else {
vErrors.push(err20);
}
errors++;
}
if(data6.symbol === undefined){
const err21 = {instancePath:instancePath+"/instrument",schemaPath:"#/$defs/InstrumentRef/required",keyword:"required",params:{missingProperty: "symbol"},message:"must have required property '"+"symbol"+"'"};
if(vErrors === null){
vErrors = [err21];
}
else {
vErrors.push(err21);
}
errors++;
}
if(data6.exchange === undefined){
const err22 = {instancePath:instancePath+"/instrument",schemaPath:"#/$defs/InstrumentRef/required",keyword:"required",params:{missingProperty: "exchange"},message:"must have required property '"+"exchange"+"'"};
if(vErrors === null){
vErrors = [err22];
}
else {
vErrors.push(err22);
}
errors++;
}
if(data6.market === undefined){
const err23 = {instancePath:instancePath+"/instrument",schemaPath:"#/$defs/InstrumentRef/required",keyword:"required",params:{missingProperty: "market"},message:"must have required property '"+"market"+"'"};
if(vErrors === null){
vErrors = [err23];
}
else {
vErrors.push(err23);
}
errors++;
}
for(const key1 in data6){
if(!((((((key1 === "asset_class") || (key1 === "currency")) || (key1 === "exchange")) || (key1 === "instrument_id")) || (key1 === "market")) || (key1 === "symbol"))){
const err24 = {instancePath:instancePath+"/instrument",schemaPath:"#/$defs/InstrumentRef/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key1},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err24];
}
else {
vErrors.push(err24);
}
errors++;
}
}
if(data6.asset_class !== undefined){
let data7 = data6.asset_class;
if(typeof data7 === "string"){
if(!pattern8.test(data7)){
const err25 = {instancePath:instancePath+"/instrument/asset_class",schemaPath:"#/$defs/InstrumentRef/properties/asset_class/pattern",keyword:"pattern",params:{pattern: "^[a-z][a-z0-9_]{1,31}$"},message:"must match pattern \""+"^[a-z][a-z0-9_]{1,31}$"+"\""};
if(vErrors === null){
vErrors = [err25];
}
else {
vErrors.push(err25);
}
errors++;
}
}
else {
const err26 = {instancePath:instancePath+"/instrument/asset_class",schemaPath:"#/$defs/InstrumentRef/properties/asset_class/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err26];
}
else {
vErrors.push(err26);
}
errors++;
}
}
if(data6.currency !== undefined){
let data8 = data6.currency;
if(typeof data8 === "string"){
if(!pattern9.test(data8)){
const err27 = {instancePath:instancePath+"/instrument/currency",schemaPath:"#/$defs/InstrumentRef/properties/currency/pattern",keyword:"pattern",params:{pattern: "^[A-Z]{3}$"},message:"must match pattern \""+"^[A-Z]{3}$"+"\""};
if(vErrors === null){
vErrors = [err27];
}
else {
vErrors.push(err27);
}
errors++;
}
}
else {
const err28 = {instancePath:instancePath+"/instrument/currency",schemaPath:"#/$defs/InstrumentRef/properties/currency/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err28];
}
else {
vErrors.push(err28);
}
errors++;
}
}
if(data6.exchange !== undefined){
let data9 = data6.exchange;
if(typeof data9 === "string"){
if(!pattern10.test(data9)){
const err29 = {instancePath:instancePath+"/instrument/exchange",schemaPath:"#/$defs/InstrumentRef/properties/exchange/pattern",keyword:"pattern",params:{pattern: "^[A-Z0-9_]{2,32}$"},message:"must match pattern \""+"^[A-Z0-9_]{2,32}$"+"\""};
if(vErrors === null){
vErrors = [err29];
}
else {
vErrors.push(err29);
}
errors++;
}
}
else {
const err30 = {instancePath:instancePath+"/instrument/exchange",schemaPath:"#/$defs/InstrumentRef/properties/exchange/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err30];
}
else {
vErrors.push(err30);
}
errors++;
}
}
if(data6.instrument_id !== undefined){
let data10 = data6.instrument_id;
if(typeof data10 === "string"){
if(!pattern11.test(data10)){
const err31 = {instancePath:instancePath+"/instrument/instrument_id",schemaPath:"#/$defs/InstrumentRef/properties/instrument_id/pattern",keyword:"pattern",params:{pattern: "^[A-Z0-9_]+:[A-Za-z0-9._-]+$"},message:"must match pattern \""+"^[A-Z0-9_]+:[A-Za-z0-9._-]+$"+"\""};
if(vErrors === null){
vErrors = [err31];
}
else {
vErrors.push(err31);
}
errors++;
}
}
else {
const err32 = {instancePath:instancePath+"/instrument/instrument_id",schemaPath:"#/$defs/InstrumentRef/properties/instrument_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err32];
}
else {
vErrors.push(err32);
}
errors++;
}
}
if(data6.market !== undefined){
let data11 = data6.market;
if(typeof data11 === "string"){
if(!pattern10.test(data11)){
const err33 = {instancePath:instancePath+"/instrument/market",schemaPath:"#/$defs/InstrumentRef/properties/market/pattern",keyword:"pattern",params:{pattern: "^[A-Z0-9_]{2,32}$"},message:"must match pattern \""+"^[A-Z0-9_]{2,32}$"+"\""};
if(vErrors === null){
vErrors = [err33];
}
else {
vErrors.push(err33);
}
errors++;
}
}
else {
const err34 = {instancePath:instancePath+"/instrument/market",schemaPath:"#/$defs/InstrumentRef/properties/market/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err34];
}
else {
vErrors.push(err34);
}
errors++;
}
}
if(data6.symbol !== undefined){
let data12 = data6.symbol;
if(typeof data12 === "string"){
if(func3(data12) > 64){
const err35 = {instancePath:instancePath+"/instrument/symbol",schemaPath:"#/$defs/InstrumentRef/properties/symbol/maxLength",keyword:"maxLength",params:{limit: 64},message:"must NOT have more than 64 characters"};
if(vErrors === null){
vErrors = [err35];
}
else {
vErrors.push(err35);
}
errors++;
}
if(func3(data12) < 1){
const err36 = {instancePath:instancePath+"/instrument/symbol",schemaPath:"#/$defs/InstrumentRef/properties/symbol/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err36];
}
else {
vErrors.push(err36);
}
errors++;
}
}
else {
const err37 = {instancePath:instancePath+"/instrument/symbol",schemaPath:"#/$defs/InstrumentRef/properties/symbol/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err37];
}
else {
vErrors.push(err37);
}
errors++;
}
}
}
else {
const err38 = {instancePath:instancePath+"/instrument",schemaPath:"#/$defs/InstrumentRef/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err38];
}
else {
vErrors.push(err38);
}
errors++;
}
}
if(data.omitted_duplicate_count !== undefined){
let data13 = data.omitted_duplicate_count;
if(!(((typeof data13 == "number") && (!(data13 % 1) && !isNaN(data13))) && (isFinite(data13)))){
const err39 = {instancePath:instancePath+"/omitted_duplicate_count",schemaPath:"#/properties/omitted_duplicate_count/type",keyword:"type",params:{type: "integer"},message:"must be integer"};
if(vErrors === null){
vErrors = [err39];
}
else {
vErrors.push(err39);
}
errors++;
}
if((typeof data13 == "number") && (isFinite(data13))){
if(data13 < 0 || isNaN(data13)){
const err40 = {instancePath:instancePath+"/omitted_duplicate_count",schemaPath:"#/properties/omitted_duplicate_count/minimum",keyword:"minimum",params:{comparison: ">=", limit: 0},message:"must be >= 0"};
if(vErrors === null){
vErrors = [err40];
}
else {
vErrors.push(err40);
}
errors++;
}
}
}
if(data.release_tier !== undefined){
let data14 = data.release_tier;
if(typeof data14 !== "string"){
const err41 = {instancePath:instancePath+"/release_tier",schemaPath:"#/$defs/SourceReleaseTier/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err41];
}
else {
vErrors.push(err41);
}
errors++;
}
if(!((data14 === "dev_only") || (data14 === "authorized"))){
const err42 = {instancePath:instancePath+"/release_tier",schemaPath:"#/$defs/SourceReleaseTier/enum",keyword:"enum",params:{allowedValues: schema39.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err42];
}
else {
vErrors.push(err42);
}
errors++;
}
}
if(data.ruleset_version !== undefined){
let data15 = data.ruleset_version;
if(typeof data15 === "string"){
if(func3(data15) > 100){
const err43 = {instancePath:instancePath+"/ruleset_version",schemaPath:"#/properties/ruleset_version/maxLength",keyword:"maxLength",params:{limit: 100},message:"must NOT have more than 100 characters"};
if(vErrors === null){
vErrors = [err43];
}
else {
vErrors.push(err43);
}
errors++;
}
if(func3(data15) < 1){
const err44 = {instancePath:instancePath+"/ruleset_version",schemaPath:"#/properties/ruleset_version/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err44];
}
else {
vErrors.push(err44);
}
errors++;
}
}
else {
const err45 = {instancePath:instancePath+"/ruleset_version",schemaPath:"#/properties/ruleset_version/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err45];
}
else {
vErrors.push(err45);
}
errors++;
}
}
if(data.runtime_mode !== undefined){
let data16 = data.runtime_mode;
if(typeof data16 !== "string"){
const err46 = {instancePath:instancePath+"/runtime_mode",schemaPath:"#/$defs/SourceRuntimeModeV1_2/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err46];
}
else {
vErrors.push(err46);
}
errors++;
}
if(!((((data16 === "demo_replay") || (data16 === "dev_live")) || (data16 === "public_static")) || (data16 === "authorized"))){
const err47 = {instancePath:instancePath+"/runtime_mode",schemaPath:"#/$defs/SourceRuntimeModeV1_2/enum",keyword:"enum",params:{allowedValues: schema41.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err47];
}
else {
vErrors.push(err47);
}
errors++;
}
}
if(data.schema_version !== undefined){
let data17 = data.schema_version;
if(typeof data17 !== "string"){
const err48 = {instancePath:instancePath+"/schema_version",schemaPath:"#/properties/schema_version/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err48];
}
else {
vErrors.push(err48);
}
errors++;
}
if("1.2" !== data17){
const err49 = {instancePath:instancePath+"/schema_version",schemaPath:"#/properties/schema_version/const",keyword:"const",params:{allowedValue: "1.2"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err49];
}
else {
vErrors.push(err49);
}
errors++;
}
}
if(data.sections !== undefined){
let data18 = data.sections;
const visitedNodes1 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate25) || new Set();
if(!visitedNodes1.has(data18)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate25, visitedNodes1);
const dataNode = data18;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes1.add(dataNode);
if(!(validate25(data18, {instancePath:instancePath+"/sections",parentData:data,parentDataProperty:"sections",rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate25.errors : vErrors.concat(validate25.errors);
errors = vErrors.length;
}
visitedNodes1.delete(dataNode);
}
}
if(data.source_coverage !== undefined){
let data19 = data.source_coverage;
if(Array.isArray(data19)){
if(data19.length > 200){
const err50 = {instancePath:instancePath+"/source_coverage",schemaPath:"#/properties/source_coverage/maxItems",keyword:"maxItems",params:{limit: 200},message:"must NOT have more than 200 items"};
if(vErrors === null){
vErrors = [err50];
}
else {
vErrors.push(err50);
}
errors++;
}
const len0 = data19.length;
for(let i0=0; i0<len0; i0++){
let data20 = data19[i0];
const visitedNodes2 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate35) || new Set();
if(!visitedNodes2.has(data20)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate35, visitedNodes2);
const dataNode = data20;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes2.add(dataNode);
if(!(validate35(data20, {instancePath:instancePath+"/source_coverage/" + i0,parentData:data19,parentDataProperty:i0,rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate35.errors : vErrors.concat(validate35.errors);
errors = vErrors.length;
}
visitedNodes2.delete(dataNode);
}
}
}
else {
const err51 = {instancePath:instancePath+"/source_coverage",schemaPath:"#/properties/source_coverage/type",keyword:"type",params:{type: "array"},message:"must be array"};
if(vErrors === null){
vErrors = [err51];
}
else {
vErrors.push(err51);
}
errors++;
}
}
}
else {
const err52 = {instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err52];
}
else {
vErrors.push(err52);
}
errors++;
}
validate20.errors = vErrors;
return errors === 0;
}
validate20.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

exports.validateEventEvidenceV1_2 = validate40;
const schema56 = {"$defs":{"BriefingChangeType":{"enum":["new","update","correction","contradiction"],"title":"BriefingChangeType","type":"string"},"DecimalFactValue":{"additionalProperties":false,"properties":{"kind":{"const":"decimal","title":"Kind","type":"string"},"value":{"pattern":"^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$","title":"Value","type":"string"}},"required":["kind","value"],"title":"DecimalFactValue","type":"object"},"EventEvidenceSourceV1_2":{"additionalProperties":false,"description":"R1 1.2 evidence metadata with complete provider lineage.","properties":{"content_sha256":{"pattern":"^[a-f0-9]{64}$","title":"Content Sha256","type":"string"},"correction_relation":{"anyOf":[{"discriminator":{"mapping":{"replacement":"#/$defs/ReplacementEvidenceRelation","retraction":"#/$defs/RetractionEvidenceRelation"},"propertyName":"kind"},"oneOf":[{"$ref":"#/$defs/ReplacementEvidenceRelation"},{"$ref":"#/$defs/RetractionEvidenceRelation"}]},{"type":"null"}],"default":null,"title":"Correction Relation"},"event_time":{"format":"date-time","title":"Event Time","type":"string"},"evidence_id":{"format":"uuid","title":"Evidence Id","type":"string"},"fetched_at":{"format":"date-time","title":"Fetched At","type":"string"},"first_seen_at":{"format":"date-time","title":"First Seen At","type":"string"},"license_id":{"maxLength":100,"minLength":1,"title":"License Id","type":"string"},"provenance":{"$ref":"#/$defs/SourceProvenanceV1_2"},"published_at":{"format":"date-time","title":"Published At","type":"string"},"revision_known_at":{"format":"date-time","title":"Revision Known At","type":"string"},"rights_snapshot_hash":{"pattern":"^[a-f0-9]{64}$","title":"Rights Snapshot Hash","type":"string"},"source_id":{"maxLength":100,"minLength":1,"title":"Source Id","type":"string"},"source_role":{"$ref":"#/$defs/SourceRole"},"source_url":{"format":"uri","maxLength":2083,"minLength":1,"title":"Source Url","type":"string"},"title":{"maxLength":500,"minLength":1,"title":"Title","type":"string"}},"required":["evidence_id","source_id","source_role","source_url","title","published_at","first_seen_at","fetched_at","revision_known_at","content_sha256","license_id","rights_snapshot_hash","event_time","provenance"],"title":"EventEvidenceSourceV1_2","type":"object","x-runtime-invariants":["published_at <= first_seen_at <= fetched_at <= revision_known_at"]},"EventFactSlot":{"additionalProperties":false,"description":"One normalized fact value with explicit evidence support.","properties":{"evidence_ids":{"items":{"format":"uuid","type":"string"},"maxItems":50,"minItems":1,"title":"Evidence Ids","type":"array"},"period":{"anyOf":[{"maxLength":100,"type":"string"},{"type":"null"}],"default":null,"title":"Period"},"slot":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Slot","type":"string"},"unit":{"anyOf":[{"maxLength":50,"type":"string"},{"type":"null"}],"default":null,"title":"Unit"},"value":{"discriminator":{"mapping":{"decimal":"#/$defs/DecimalFactValue","text":"#/$defs/TextFactValue"},"propertyName":"kind"},"oneOf":[{"$ref":"#/$defs/TextFactValue"},{"$ref":"#/$defs/DecimalFactValue"}],"title":"Value"}},"required":["slot","value","evidence_ids"],"title":"EventFactSlot","type":"object","x-runtime-invariants":["evidence_ids entries are unique"]},"EventRevisionTimeline":{"additionalProperties":false,"description":"A descending, bounded page of revision identities.","properties":{"before_revision":{"anyOf":[{"minimum":1,"type":"integer"},{"type":"null"}],"default":null,"title":"Before Revision"},"has_older":{"title":"Has Older","type":"boolean"},"items":{"items":{"$ref":"#/$defs/EventRevisionTimelineItem"},"maxItems":50,"title":"Items","type":"array"},"next_before_revision":{"anyOf":[{"minimum":1,"type":"integer"},{"type":"null"}],"default":null,"title":"Next Before Revision"}},"required":["has_older"],"title":"EventRevisionTimeline","type":"object","x-runtime-invariants":["items revisions are unique and strictly descending","before_revision=null is the first page and must not be empty","continuation page items have revision < before_revision","an empty page is permitted only for before_revision=1 termination","has_older=true requires next_before_revision to equal the oldest returned revision and to be greater than 1","has_older=false requires next_before_revision=null"]},"EventRevisionTimelineItem":{"additionalProperties":false,"description":"One immutable revision identity safe to expose in the evidence timeline.","properties":{"audit_id":{"format":"uuid","title":"Audit Id","type":"string"},"change_type":{"$ref":"#/$defs/BriefingChangeType"},"extraction_version":{"maxLength":100,"minLength":1,"title":"Extraction Version","type":"string"},"revision":{"minimum":1,"title":"Revision","type":"integer"},"revision_known_at":{"format":"date-time","title":"Revision Known At","type":"string"},"ruleset_version":{"maxLength":100,"minLength":1,"title":"Ruleset Version","type":"string"},"supersedes_revision":{"anyOf":[{"minimum":1,"type":"integer"},{"type":"null"}],"default":null,"title":"Supersedes Revision"}},"required":["revision","change_type","revision_known_at","extraction_version","ruleset_version","audit_id"],"title":"EventRevisionTimelineItem","type":"object"},"ReplacementCorrection":{"additionalProperties":false,"description":"Directional old-to-new fact replacement with evidence on both sides.","properties":{"current_evidence_ids":{"items":{"format":"uuid","type":"string"},"maxItems":50,"minItems":1,"title":"Current Evidence Ids","type":"array"},"current_value":{"discriminator":{"mapping":{"decimal":"#/$defs/DecimalFactValue","text":"#/$defs/TextFactValue"},"propertyName":"kind"},"oneOf":[{"$ref":"#/$defs/TextFactValue"},{"$ref":"#/$defs/DecimalFactValue"}],"title":"Current Value"},"kind":{"const":"replacement","title":"Kind","type":"string"},"period":{"anyOf":[{"maxLength":100,"type":"string"},{"type":"null"}],"default":null,"title":"Period"},"previous_evidence_ids":{"items":{"format":"uuid","type":"string"},"maxItems":50,"minItems":1,"title":"Previous Evidence Ids","type":"array"},"previous_value":{"discriminator":{"mapping":{"decimal":"#/$defs/DecimalFactValue","text":"#/$defs/TextFactValue"},"propertyName":"kind"},"oneOf":[{"$ref":"#/$defs/TextFactValue"},{"$ref":"#/$defs/DecimalFactValue"}],"title":"Previous Value"},"slot":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Slot","type":"string"},"unit":{"anyOf":[{"maxLength":50,"type":"string"},{"type":"null"}],"default":null,"title":"Unit"}},"required":["kind","slot","previous_value","current_value","previous_evidence_ids","current_evidence_ids"],"title":"ReplacementCorrection","type":"object"},"ReplacementEvidenceRelation":{"additionalProperties":false,"description":"A current evidence record explicitly replaces a prior record.","properties":{"kind":{"const":"replacement","title":"Kind","type":"string"},"prior_evidence_id":{"format":"uuid","title":"Prior Evidence Id","type":"string"}},"required":["kind","prior_evidence_id"],"title":"ReplacementEvidenceRelation","type":"object"},"RetractionCorrection":{"additionalProperties":false,"description":"Directional retraction with prior-value and retraction-notice evidence.","properties":{"current_evidence_ids":{"items":{"format":"uuid","type":"string"},"maxItems":50,"minItems":1,"title":"Current Evidence Ids","type":"array"},"current_value":{"const":null,"default":null,"title":"Current Value","type":"null"},"kind":{"const":"retraction","title":"Kind","type":"string"},"period":{"anyOf":[{"maxLength":100,"type":"string"},{"type":"null"}],"default":null,"title":"Period"},"previous_evidence_ids":{"items":{"format":"uuid","type":"string"},"maxItems":50,"minItems":1,"title":"Previous Evidence Ids","type":"array"},"previous_value":{"discriminator":{"mapping":{"decimal":"#/$defs/DecimalFactValue","text":"#/$defs/TextFactValue"},"propertyName":"kind"},"oneOf":[{"$ref":"#/$defs/TextFactValue"},{"$ref":"#/$defs/DecimalFactValue"}],"title":"Previous Value"},"slot":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Slot","type":"string"},"unit":{"anyOf":[{"maxLength":50,"type":"string"},{"type":"null"}],"default":null,"title":"Unit"}},"required":["kind","slot","previous_value","previous_evidence_ids","current_evidence_ids"],"title":"RetractionCorrection","type":"object"},"RetractionEvidenceRelation":{"additionalProperties":false,"description":"A current evidence record explicitly retracts a prior record.","properties":{"kind":{"const":"retraction","title":"Kind","type":"string"},"prior_evidence_id":{"format":"uuid","title":"Prior Evidence Id","type":"string"}},"required":["kind","prior_evidence_id"],"title":"RetractionEvidenceRelation","type":"object"},"SourceAccessChannelV1_2":{"description":"R1 1.2 access channels used by replay, free and authorized adapters.","enum":["demo_replay","akshare","official_http","baostock_sdk","authorized_api"],"title":"SourceAccessChannelV1_2","type":"string"},"SourceEvidenceRole":{"description":"How one source may support a fact without changing its source identity.","enum":["authoritative","structured_helper","discovery","cross_check"],"title":"SourceEvidenceRole","type":"string"},"SourceProvenanceV1_2":{"additionalProperties":false,"allOf":[{"if":{"properties":{"runtime_mode":{"const":"demo_replay"}}},"then":{"properties":{"access_channel":{"const":"demo_replay"},"quality_tier":{"const":"demo_replay"},"release_tier":{"const":"dev_only"},"rights_status":{"const":"not_applicable"}}}},{"if":{"properties":{"runtime_mode":{"const":"public_static"}}},"then":{"properties":{"access_channel":{"const":"demo_replay"},"quality_tier":{"const":"demo_replay"},"release_tier":{"const":"dev_only"},"rights_status":{"const":"not_applicable"}}}},{"if":{"properties":{"runtime_mode":{"const":"dev_live"}}},"then":{"properties":{"access_channel":{"enum":["akshare","official_http","baostock_sdk"]},"quality_tier":{"enum":["free_official","free_structured","dev_web"]},"release_tier":{"const":"dev_only"},"rights_status":{"const":"unverified"}}}},{"if":{"properties":{"runtime_mode":{"const":"authorized"}}},"then":{"properties":{"access_channel":{"const":"authorized_api"},"quality_tier":{"const":"authorized"},"release_tier":{"const":"authorized"},"rights_status":{"const":"approved"}}}}],"description":"R1 1.2 lineage with explicit quality and evidence-role dimensions.","properties":{"access_channel":{"$ref":"#/$defs/SourceAccessChannelV1_2"},"adapter_name":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Adapter Name","type":"string"},"adapter_version":{"maxLength":100,"minLength":1,"title":"Adapter Version","type":"string"},"endpoint_name":{"pattern":"^[a-z][a-z0-9_.-]{1,199}$","title":"Endpoint Name","type":"string"},"evidence_role":{"$ref":"#/$defs/SourceEvidenceRole"},"provider":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Provider","type":"string"},"quality_tier":{"$ref":"#/$defs/SourceQualityTier"},"release_tier":{"$ref":"#/$defs/SourceReleaseTier"},"rights_status":{"$ref":"#/$defs/SourceRightsStatusV1_2"},"runtime_mode":{"$ref":"#/$defs/SourceRuntimeModeV1_2"},"schema_version":{"const":"1.2","default":"1.2","title":"Schema Version","type":"string"},"source_identifier":{"maxLength":500,"minLength":1,"title":"Source Identifier","type":"string"}},"required":["schema_version","provider","access_channel","adapter_name","adapter_version","endpoint_name","source_identifier","runtime_mode","release_tier","rights_status","quality_tier","evidence_role"],"title":"SourceProvenanceV1_2","type":"object","x-runtime-invariants":["runtime_mode constrains channel, release tier, rights status, and quality","quality_tier and evidence_role remain independent dimensions"]},"SourceQualityTier":{"description":"Coarse quality origin kept separate from evidence authority and rights.","enum":["demo_replay","free_official","free_structured","dev_web","authorized"],"title":"SourceQualityTier","type":"string"},"SourceReleaseTier":{"description":"Whether a source result may leave development-only surfaces.","enum":["dev_only","authorized"],"title":"SourceReleaseTier","type":"string"},"SourceRightsStatusV1_2":{"description":"R1 1.2 public rights state, including data with no source-rights claim.","enum":["unverified","approved","blocked","not_applicable"],"title":"SourceRightsStatusV1_2","type":"string"},"SourceRole":{"description":"Permitted public source roles.","enum":["statutory","professional_market","professional_information","discovery"],"title":"SourceRole","type":"string"},"SourceRuntimeModeV1_2":{"description":"R1 1.2 execution modes, including the detached public snapshot.","enum":["demo_replay","dev_live","public_static","authorized"],"title":"SourceRuntimeModeV1_2","type":"string"},"TextFactValue":{"additionalProperties":false,"properties":{"kind":{"const":"text","title":"Kind","type":"string"},"value":{"maxLength":500,"minLength":1,"title":"Value","type":"string"}},"required":["kind","value"],"title":"TextFactValue","type":"object"}},"$id":"https://stock-market-agent.local/schemas/events/event-evidence-view.v1.2.schema.json","$schema":"https://json-schema.org/draft/2020-12/schema","additionalProperties":false,"description":"R1 1.2 evidence response with one explicit source runtime mode.","properties":{"as_of":{"format":"date-time","title":"As Of","type":"string"},"audit_id":{"format":"uuid","title":"Audit Id","type":"string"},"change_type":{"$ref":"#/$defs/BriefingChangeType"},"cluster_id":{"format":"uuid","title":"Cluster Id","type":"string"},"correction":{"anyOf":[{"discriminator":{"mapping":{"replacement":"#/$defs/ReplacementCorrection","retraction":"#/$defs/RetractionCorrection"},"propertyName":"kind"},"oneOf":[{"$ref":"#/$defs/ReplacementCorrection"},{"$ref":"#/$defs/RetractionCorrection"}]},{"type":"null"}],"default":null,"title":"Correction"},"event_id":{"format":"uuid","title":"Event Id","type":"string"},"event_type":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Event Type","type":"string"},"evidence":{"items":{"$ref":"#/$defs/EventEvidenceSourceV1_2"},"maxItems":50,"minItems":1,"title":"Evidence","type":"array"},"extraction_version":{"maxLength":100,"minLength":1,"title":"Extraction Version","type":"string"},"facts":{"items":{"$ref":"#/$defs/EventFactSlot"},"maxItems":100,"title":"Facts","type":"array"},"instrument_ids":{"items":{"type":"string"},"maxItems":100,"minItems":1,"title":"Instrument Ids","type":"array"},"revision":{"minimum":1,"title":"Revision","type":"integer"},"revision_known_at":{"format":"date-time","title":"Revision Known At","type":"string"},"revision_timeline":{"$ref":"#/$defs/EventRevisionTimeline"},"ruleset_version":{"maxLength":100,"minLength":1,"title":"Ruleset Version","type":"string"},"runtime_mode":{"$ref":"#/$defs/SourceRuntimeModeV1_2"},"schema_version":{"const":"1.2","default":"1.2","title":"Schema Version","type":"string"},"supersedes_revision":{"anyOf":[{"minimum":1,"type":"integer"},{"type":"null"}],"default":null,"title":"Supersedes Revision"},"title":{"maxLength":500,"minLength":1,"title":"Title","type":"string"}},"required":["schema_version","event_id","cluster_id","revision","change_type","instrument_ids","event_type","title","as_of","revision_known_at","extraction_version","ruleset_version","audit_id","revision_timeline","evidence","runtime_mode"],"title":"EventEvidenceViewV1_2","type":"object","x-contract-model":"stock_assistant.contracts.event_views.EventEvidenceViewV1_2","x-runtime-invariants":["instrument_ids entries are unique","evidence evidence_id values are unique","all fact evidence_ids reference evidence records in this response","all evidence.revision_known_at values are <= as_of","selected revision_known_at is <= as_of and covers all selected evidence","the first revision_timeline page contains the selected revision identity","any revision_timeline page containing the selected revision matches its identity exactly","all revision_timeline.items revision_known_at values are <= as_of","correction and contradiction require at least two evidence records","new requires revision=1 and supersedes_revision=null","update, correction and contradiction require revision>1 and supersedes_revision<revision","correction requires directional replacement/retraction values and matching evidence relations; replacement facts bind only to their previous or current evidence side","every correction_relation is a closed edge from a registered current evidence record to a distinct registered previous evidence record in this response","contradiction requires semantically distinct values for the same slot/period/unit and every conflicting value has evidence not used by the other conflicting values"],"x-schema-version":"1.2"};
const schema57 = {"enum":["new","update","correction","contradiction"],"title":"BriefingChangeType","type":"string"};
const schema75 = {"description":"R1 1.2 execution modes, including the detached public snapshot.","enum":["demo_replay","dev_live","public_static","authorized"],"title":"SourceRuntimeModeV1_2","type":"string"};
const schema58 = {"additionalProperties":false,"description":"Directional old-to-new fact replacement with evidence on both sides.","properties":{"current_evidence_ids":{"items":{"format":"uuid","type":"string"},"maxItems":50,"minItems":1,"title":"Current Evidence Ids","type":"array"},"current_value":{"discriminator":{"mapping":{"decimal":"#/$defs/DecimalFactValue","text":"#/$defs/TextFactValue"},"propertyName":"kind"},"oneOf":[{"$ref":"#/$defs/TextFactValue"},{"$ref":"#/$defs/DecimalFactValue"}],"title":"Current Value"},"kind":{"const":"replacement","title":"Kind","type":"string"},"period":{"anyOf":[{"maxLength":100,"type":"string"},{"type":"null"}],"default":null,"title":"Period"},"previous_evidence_ids":{"items":{"format":"uuid","type":"string"},"maxItems":50,"minItems":1,"title":"Previous Evidence Ids","type":"array"},"previous_value":{"discriminator":{"mapping":{"decimal":"#/$defs/DecimalFactValue","text":"#/$defs/TextFactValue"},"propertyName":"kind"},"oneOf":[{"$ref":"#/$defs/TextFactValue"},{"$ref":"#/$defs/DecimalFactValue"}],"title":"Previous Value"},"slot":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Slot","type":"string"},"unit":{"anyOf":[{"maxLength":50,"type":"string"},{"type":"null"}],"default":null,"title":"Unit"}},"required":["kind","slot","previous_value","current_value","previous_evidence_ids","current_evidence_ids"],"title":"ReplacementCorrection","type":"object"};
const schema59 = {"additionalProperties":false,"properties":{"kind":{"const":"text","title":"Kind","type":"string"},"value":{"maxLength":500,"minLength":1,"title":"Value","type":"string"}},"required":["kind","value"],"title":"TextFactValue","type":"object"};
const schema60 = {"additionalProperties":false,"properties":{"kind":{"const":"decimal","title":"Kind","type":"string"},"value":{"pattern":"^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$","title":"Value","type":"string"}},"required":["kind","value"],"title":"DecimalFactValue","type":"object"};
const pattern14 = new RegExp("^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$", "u");

function validate41(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}, isAllOfVariant = 0}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate41.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.kind === undefined){
const err0 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "kind"},message:"must have required property '"+"kind"+"'"};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
if(data.slot === undefined){
const err1 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "slot"},message:"must have required property '"+"slot"+"'"};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
if(data.previous_value === undefined){
const err2 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "previous_value"},message:"must have required property '"+"previous_value"+"'"};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
if(data.current_value === undefined){
const err3 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "current_value"},message:"must have required property '"+"current_value"+"'"};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
}
if(data.previous_evidence_ids === undefined){
const err4 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "previous_evidence_ids"},message:"must have required property '"+"previous_evidence_ids"+"'"};
if(vErrors === null){
vErrors = [err4];
}
else {
vErrors.push(err4);
}
errors++;
}
if(data.current_evidence_ids === undefined){
const err5 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "current_evidence_ids"},message:"must have required property '"+"current_evidence_ids"+"'"};
if(vErrors === null){
vErrors = [err5];
}
else {
vErrors.push(err5);
}
errors++;
}
for(const key0 in data){
if(!((((((((key0 === "current_evidence_ids") || (key0 === "current_value")) || (key0 === "kind")) || (key0 === "period")) || (key0 === "previous_evidence_ids")) || (key0 === "previous_value")) || (key0 === "slot")) || (key0 === "unit"))){
const err6 = {instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err6];
}
else {
vErrors.push(err6);
}
errors++;
}
}
if(data.current_evidence_ids !== undefined){
let data0 = data.current_evidence_ids;
if(Array.isArray(data0)){
if(data0.length > 50){
const err7 = {instancePath:instancePath+"/current_evidence_ids",schemaPath:"#/properties/current_evidence_ids/maxItems",keyword:"maxItems",params:{limit: 50},message:"must NOT have more than 50 items"};
if(vErrors === null){
vErrors = [err7];
}
else {
vErrors.push(err7);
}
errors++;
}
if(data0.length < 1){
const err8 = {instancePath:instancePath+"/current_evidence_ids",schemaPath:"#/properties/current_evidence_ids/minItems",keyword:"minItems",params:{limit: 1},message:"must NOT have fewer than 1 items"};
if(vErrors === null){
vErrors = [err8];
}
else {
vErrors.push(err8);
}
errors++;
}
const len0 = data0.length;
for(let i0=0; i0<len0; i0++){
let data1 = data0[i0];
if(typeof data1 === "string"){
if(!(formats2.test(data1))){
const err9 = {instancePath:instancePath+"/current_evidence_ids/" + i0,schemaPath:"#/properties/current_evidence_ids/items/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err9];
}
else {
vErrors.push(err9);
}
errors++;
}
}
else {
const err10 = {instancePath:instancePath+"/current_evidence_ids/" + i0,schemaPath:"#/properties/current_evidence_ids/items/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err10];
}
else {
vErrors.push(err10);
}
errors++;
}
}
}
else {
const err11 = {instancePath:instancePath+"/current_evidence_ids",schemaPath:"#/properties/current_evidence_ids/type",keyword:"type",params:{type: "array"},message:"must be array"};
if(vErrors === null){
vErrors = [err11];
}
else {
vErrors.push(err11);
}
errors++;
}
}
if(data.current_value !== undefined){
let data2 = data.current_value;
if(data2 && typeof data2 == "object" && !Array.isArray(data2)){
const tag0 = data2.kind;
if(typeof tag0 == "string"){
if(tag0 === "text"){
if(data2 && typeof data2 == "object" && !Array.isArray(data2)){
if(data2.kind === undefined){
const err12 = {instancePath:instancePath+"/current_value",schemaPath:"#/$defs/TextFactValue/required",keyword:"required",params:{missingProperty: "kind"},message:"must have required property '"+"kind"+"'"};
if(vErrors === null){
vErrors = [err12];
}
else {
vErrors.push(err12);
}
errors++;
}
if(data2.value === undefined){
const err13 = {instancePath:instancePath+"/current_value",schemaPath:"#/$defs/TextFactValue/required",keyword:"required",params:{missingProperty: "value"},message:"must have required property '"+"value"+"'"};
if(vErrors === null){
vErrors = [err13];
}
else {
vErrors.push(err13);
}
errors++;
}
for(const key1 in data2){
if(!((key1 === "kind") || (key1 === "value"))){
const err14 = {instancePath:instancePath+"/current_value",schemaPath:"#/$defs/TextFactValue/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key1},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err14];
}
else {
vErrors.push(err14);
}
errors++;
}
}
if(data2.kind !== undefined){
let data3 = data2.kind;
if(typeof data3 !== "string"){
const err15 = {instancePath:instancePath+"/current_value/kind",schemaPath:"#/$defs/TextFactValue/properties/kind/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err15];
}
else {
vErrors.push(err15);
}
errors++;
}
if("text" !== data3){
const err16 = {instancePath:instancePath+"/current_value/kind",schemaPath:"#/$defs/TextFactValue/properties/kind/const",keyword:"const",params:{allowedValue: "text"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err16];
}
else {
vErrors.push(err16);
}
errors++;
}
}
if(data2.value !== undefined){
let data4 = data2.value;
if(typeof data4 === "string"){
if(func3(data4) > 500){
const err17 = {instancePath:instancePath+"/current_value/value",schemaPath:"#/$defs/TextFactValue/properties/value/maxLength",keyword:"maxLength",params:{limit: 500},message:"must NOT have more than 500 characters"};
if(vErrors === null){
vErrors = [err17];
}
else {
vErrors.push(err17);
}
errors++;
}
if(func3(data4) < 1){
const err18 = {instancePath:instancePath+"/current_value/value",schemaPath:"#/$defs/TextFactValue/properties/value/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err18];
}
else {
vErrors.push(err18);
}
errors++;
}
}
else {
const err19 = {instancePath:instancePath+"/current_value/value",schemaPath:"#/$defs/TextFactValue/properties/value/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err19];
}
else {
vErrors.push(err19);
}
errors++;
}
}
}
else {
const err20 = {instancePath:instancePath+"/current_value",schemaPath:"#/$defs/TextFactValue/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err20];
}
else {
vErrors.push(err20);
}
errors++;
}
var props0 = true;
}
else if(tag0 === "decimal"){
if(data2 && typeof data2 == "object" && !Array.isArray(data2)){
if(data2.kind === undefined){
const err21 = {instancePath:instancePath+"/current_value",schemaPath:"#/$defs/DecimalFactValue/required",keyword:"required",params:{missingProperty: "kind"},message:"must have required property '"+"kind"+"'"};
if(vErrors === null){
vErrors = [err21];
}
else {
vErrors.push(err21);
}
errors++;
}
if(data2.value === undefined){
const err22 = {instancePath:instancePath+"/current_value",schemaPath:"#/$defs/DecimalFactValue/required",keyword:"required",params:{missingProperty: "value"},message:"must have required property '"+"value"+"'"};
if(vErrors === null){
vErrors = [err22];
}
else {
vErrors.push(err22);
}
errors++;
}
for(const key2 in data2){
if(!((key2 === "kind") || (key2 === "value"))){
const err23 = {instancePath:instancePath+"/current_value",schemaPath:"#/$defs/DecimalFactValue/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key2},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err23];
}
else {
vErrors.push(err23);
}
errors++;
}
}
if(data2.kind !== undefined){
let data5 = data2.kind;
if(typeof data5 !== "string"){
const err24 = {instancePath:instancePath+"/current_value/kind",schemaPath:"#/$defs/DecimalFactValue/properties/kind/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err24];
}
else {
vErrors.push(err24);
}
errors++;
}
if("decimal" !== data5){
const err25 = {instancePath:instancePath+"/current_value/kind",schemaPath:"#/$defs/DecimalFactValue/properties/kind/const",keyword:"const",params:{allowedValue: "decimal"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err25];
}
else {
vErrors.push(err25);
}
errors++;
}
}
if(data2.value !== undefined){
let data6 = data2.value;
if(typeof data6 === "string"){
if(!pattern14.test(data6)){
const err26 = {instancePath:instancePath+"/current_value/value",schemaPath:"#/$defs/DecimalFactValue/properties/value/pattern",keyword:"pattern",params:{pattern: "^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$"},message:"must match pattern \""+"^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$"+"\""};
if(vErrors === null){
vErrors = [err26];
}
else {
vErrors.push(err26);
}
errors++;
}
}
else {
const err27 = {instancePath:instancePath+"/current_value/value",schemaPath:"#/$defs/DecimalFactValue/properties/value/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err27];
}
else {
vErrors.push(err27);
}
errors++;
}
}
}
else {
const err28 = {instancePath:instancePath+"/current_value",schemaPath:"#/$defs/DecimalFactValue/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err28];
}
else {
vErrors.push(err28);
}
errors++;
}
if(props0 !== true){
props0 = true;
}
}
else {
const err29 = {instancePath:instancePath+"/current_value",schemaPath:"#/properties/current_value/discriminator",keyword:"discriminator",params:{error: "mapping", tag: "kind", tagValue: tag0},message:"value of tag \"kind\" must be in oneOf or anyOf"};
if(vErrors === null){
vErrors = [err29];
}
else {
vErrors.push(err29);
}
errors++;
}
}
else {
const err30 = {instancePath:instancePath+"/current_value",schemaPath:"#/properties/current_value/discriminator",keyword:"discriminator",params:{error: "tag", tag: "kind", tagValue: tag0},message:"tag \"kind\" must be string"};
if(vErrors === null){
vErrors = [err30];
}
else {
vErrors.push(err30);
}
errors++;
}
}
}
if(data.kind !== undefined){
let data7 = data.kind;
if(typeof data7 !== "string"){
const err31 = {instancePath:instancePath+"/kind",schemaPath:"#/properties/kind/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err31];
}
else {
vErrors.push(err31);
}
errors++;
}
if("replacement" !== data7){
const err32 = {instancePath:instancePath+"/kind",schemaPath:"#/properties/kind/const",keyword:"const",params:{allowedValue: "replacement"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err32];
}
else {
vErrors.push(err32);
}
errors++;
}
}
if(data.period !== undefined){
let data8 = data.period;
const _errs27 = errors;
let valid10 = false;
const _errs28 = errors;
if(typeof data8 === "string"){
if(func3(data8) > 100){
const err33 = {instancePath:instancePath+"/period",schemaPath:"#/properties/period/anyOf/0/maxLength",keyword:"maxLength",params:{limit: 100},message:"must NOT have more than 100 characters"};
if(vErrors === null){
vErrors = [err33];
}
else {
vErrors.push(err33);
}
errors++;
}
}
else {
const err34 = {instancePath:instancePath+"/period",schemaPath:"#/properties/period/anyOf/0/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err34];
}
else {
vErrors.push(err34);
}
errors++;
}
var _valid0 = _errs28 === errors;
valid10 = valid10 || _valid0;
const _errs30 = errors;
if(data8 !== null){
const err35 = {instancePath:instancePath+"/period",schemaPath:"#/properties/period/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err35];
}
else {
vErrors.push(err35);
}
errors++;
}
var _valid0 = _errs30 === errors;
valid10 = valid10 || _valid0;
if(!valid10){
const err36 = {instancePath:instancePath+"/period",schemaPath:"#/properties/period/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err36];
}
else {
vErrors.push(err36);
}
errors++;
}
else {
errors = _errs27;
if(vErrors !== null){
if(_errs27){
vErrors.length = _errs27;
}
else {
vErrors = null;
}
}
}
}
if(data.previous_evidence_ids !== undefined){
let data9 = data.previous_evidence_ids;
if(Array.isArray(data9)){
if(data9.length > 50){
const err37 = {instancePath:instancePath+"/previous_evidence_ids",schemaPath:"#/properties/previous_evidence_ids/maxItems",keyword:"maxItems",params:{limit: 50},message:"must NOT have more than 50 items"};
if(vErrors === null){
vErrors = [err37];
}
else {
vErrors.push(err37);
}
errors++;
}
if(data9.length < 1){
const err38 = {instancePath:instancePath+"/previous_evidence_ids",schemaPath:"#/properties/previous_evidence_ids/minItems",keyword:"minItems",params:{limit: 1},message:"must NOT have fewer than 1 items"};
if(vErrors === null){
vErrors = [err38];
}
else {
vErrors.push(err38);
}
errors++;
}
const len1 = data9.length;
for(let i1=0; i1<len1; i1++){
let data10 = data9[i1];
if(typeof data10 === "string"){
if(!(formats2.test(data10))){
const err39 = {instancePath:instancePath+"/previous_evidence_ids/" + i1,schemaPath:"#/properties/previous_evidence_ids/items/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err39];
}
else {
vErrors.push(err39);
}
errors++;
}
}
else {
const err40 = {instancePath:instancePath+"/previous_evidence_ids/" + i1,schemaPath:"#/properties/previous_evidence_ids/items/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err40];
}
else {
vErrors.push(err40);
}
errors++;
}
}
}
else {
const err41 = {instancePath:instancePath+"/previous_evidence_ids",schemaPath:"#/properties/previous_evidence_ids/type",keyword:"type",params:{type: "array"},message:"must be array"};
if(vErrors === null){
vErrors = [err41];
}
else {
vErrors.push(err41);
}
errors++;
}
}
if(data.previous_value !== undefined){
let data11 = data.previous_value;
if(data11 && typeof data11 == "object" && !Array.isArray(data11)){
const tag1 = data11.kind;
if(typeof tag1 == "string"){
if(tag1 === "text"){
if(data11 && typeof data11 == "object" && !Array.isArray(data11)){
if(data11.kind === undefined){
const err42 = {instancePath:instancePath+"/previous_value",schemaPath:"#/$defs/TextFactValue/required",keyword:"required",params:{missingProperty: "kind"},message:"must have required property '"+"kind"+"'"};
if(vErrors === null){
vErrors = [err42];
}
else {
vErrors.push(err42);
}
errors++;
}
if(data11.value === undefined){
const err43 = {instancePath:instancePath+"/previous_value",schemaPath:"#/$defs/TextFactValue/required",keyword:"required",params:{missingProperty: "value"},message:"must have required property '"+"value"+"'"};
if(vErrors === null){
vErrors = [err43];
}
else {
vErrors.push(err43);
}
errors++;
}
for(const key3 in data11){
if(!((key3 === "kind") || (key3 === "value"))){
const err44 = {instancePath:instancePath+"/previous_value",schemaPath:"#/$defs/TextFactValue/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key3},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err44];
}
else {
vErrors.push(err44);
}
errors++;
}
}
if(data11.kind !== undefined){
let data12 = data11.kind;
if(typeof data12 !== "string"){
const err45 = {instancePath:instancePath+"/previous_value/kind",schemaPath:"#/$defs/TextFactValue/properties/kind/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err45];
}
else {
vErrors.push(err45);
}
errors++;
}
if("text" !== data12){
const err46 = {instancePath:instancePath+"/previous_value/kind",schemaPath:"#/$defs/TextFactValue/properties/kind/const",keyword:"const",params:{allowedValue: "text"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err46];
}
else {
vErrors.push(err46);
}
errors++;
}
}
if(data11.value !== undefined){
let data13 = data11.value;
if(typeof data13 === "string"){
if(func3(data13) > 500){
const err47 = {instancePath:instancePath+"/previous_value/value",schemaPath:"#/$defs/TextFactValue/properties/value/maxLength",keyword:"maxLength",params:{limit: 500},message:"must NOT have more than 500 characters"};
if(vErrors === null){
vErrors = [err47];
}
else {
vErrors.push(err47);
}
errors++;
}
if(func3(data13) < 1){
const err48 = {instancePath:instancePath+"/previous_value/value",schemaPath:"#/$defs/TextFactValue/properties/value/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err48];
}
else {
vErrors.push(err48);
}
errors++;
}
}
else {
const err49 = {instancePath:instancePath+"/previous_value/value",schemaPath:"#/$defs/TextFactValue/properties/value/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err49];
}
else {
vErrors.push(err49);
}
errors++;
}
}
}
else {
const err50 = {instancePath:instancePath+"/previous_value",schemaPath:"#/$defs/TextFactValue/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err50];
}
else {
vErrors.push(err50);
}
errors++;
}
var props1 = true;
}
else if(tag1 === "decimal"){
if(data11 && typeof data11 == "object" && !Array.isArray(data11)){
if(data11.kind === undefined){
const err51 = {instancePath:instancePath+"/previous_value",schemaPath:"#/$defs/DecimalFactValue/required",keyword:"required",params:{missingProperty: "kind"},message:"must have required property '"+"kind"+"'"};
if(vErrors === null){
vErrors = [err51];
}
else {
vErrors.push(err51);
}
errors++;
}
if(data11.value === undefined){
const err52 = {instancePath:instancePath+"/previous_value",schemaPath:"#/$defs/DecimalFactValue/required",keyword:"required",params:{missingProperty: "value"},message:"must have required property '"+"value"+"'"};
if(vErrors === null){
vErrors = [err52];
}
else {
vErrors.push(err52);
}
errors++;
}
for(const key4 in data11){
if(!((key4 === "kind") || (key4 === "value"))){
const err53 = {instancePath:instancePath+"/previous_value",schemaPath:"#/$defs/DecimalFactValue/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key4},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err53];
}
else {
vErrors.push(err53);
}
errors++;
}
}
if(data11.kind !== undefined){
let data14 = data11.kind;
if(typeof data14 !== "string"){
const err54 = {instancePath:instancePath+"/previous_value/kind",schemaPath:"#/$defs/DecimalFactValue/properties/kind/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err54];
}
else {
vErrors.push(err54);
}
errors++;
}
if("decimal" !== data14){
const err55 = {instancePath:instancePath+"/previous_value/kind",schemaPath:"#/$defs/DecimalFactValue/properties/kind/const",keyword:"const",params:{allowedValue: "decimal"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err55];
}
else {
vErrors.push(err55);
}
errors++;
}
}
if(data11.value !== undefined){
let data15 = data11.value;
if(typeof data15 === "string"){
if(!pattern14.test(data15)){
const err56 = {instancePath:instancePath+"/previous_value/value",schemaPath:"#/$defs/DecimalFactValue/properties/value/pattern",keyword:"pattern",params:{pattern: "^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$"},message:"must match pattern \""+"^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$"+"\""};
if(vErrors === null){
vErrors = [err56];
}
else {
vErrors.push(err56);
}
errors++;
}
}
else {
const err57 = {instancePath:instancePath+"/previous_value/value",schemaPath:"#/$defs/DecimalFactValue/properties/value/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err57];
}
else {
vErrors.push(err57);
}
errors++;
}
}
}
else {
const err58 = {instancePath:instancePath+"/previous_value",schemaPath:"#/$defs/DecimalFactValue/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err58];
}
else {
vErrors.push(err58);
}
errors++;
}
if(props1 !== true){
props1 = true;
}
}
else {
const err59 = {instancePath:instancePath+"/previous_value",schemaPath:"#/properties/previous_value/discriminator",keyword:"discriminator",params:{error: "mapping", tag: "kind", tagValue: tag1},message:"value of tag \"kind\" must be in oneOf or anyOf"};
if(vErrors === null){
vErrors = [err59];
}
else {
vErrors.push(err59);
}
errors++;
}
}
else {
const err60 = {instancePath:instancePath+"/previous_value",schemaPath:"#/properties/previous_value/discriminator",keyword:"discriminator",params:{error: "tag", tag: "kind", tagValue: tag1},message:"tag \"kind\" must be string"};
if(vErrors === null){
vErrors = [err60];
}
else {
vErrors.push(err60);
}
errors++;
}
}
}
if(data.slot !== undefined){
let data16 = data.slot;
if(typeof data16 === "string"){
if(!pattern4.test(data16)){
const err61 = {instancePath:instancePath+"/slot",schemaPath:"#/properties/slot/pattern",keyword:"pattern",params:{pattern: "^[a-z][a-z0-9_.-]{1,99}$"},message:"must match pattern \""+"^[a-z][a-z0-9_.-]{1,99}$"+"\""};
if(vErrors === null){
vErrors = [err61];
}
else {
vErrors.push(err61);
}
errors++;
}
}
else {
const err62 = {instancePath:instancePath+"/slot",schemaPath:"#/properties/slot/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err62];
}
else {
vErrors.push(err62);
}
errors++;
}
}
if(data.unit !== undefined){
let data17 = data.unit;
const _errs57 = errors;
let valid20 = false;
const _errs58 = errors;
if(typeof data17 === "string"){
if(func3(data17) > 50){
const err63 = {instancePath:instancePath+"/unit",schemaPath:"#/properties/unit/anyOf/0/maxLength",keyword:"maxLength",params:{limit: 50},message:"must NOT have more than 50 characters"};
if(vErrors === null){
vErrors = [err63];
}
else {
vErrors.push(err63);
}
errors++;
}
}
else {
const err64 = {instancePath:instancePath+"/unit",schemaPath:"#/properties/unit/anyOf/0/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err64];
}
else {
vErrors.push(err64);
}
errors++;
}
var _valid1 = _errs58 === errors;
valid20 = valid20 || _valid1;
const _errs60 = errors;
if(data17 !== null){
const err65 = {instancePath:instancePath+"/unit",schemaPath:"#/properties/unit/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err65];
}
else {
vErrors.push(err65);
}
errors++;
}
var _valid1 = _errs60 === errors;
valid20 = valid20 || _valid1;
if(!valid20){
const err66 = {instancePath:instancePath+"/unit",schemaPath:"#/properties/unit/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err66];
}
else {
vErrors.push(err66);
}
errors++;
}
else {
errors = _errs57;
if(vErrors !== null){
if(_errs57){
vErrors.length = _errs57;
}
else {
vErrors = null;
}
}
}
}
}
else {
const err67 = {instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err67];
}
else {
vErrors.push(err67);
}
errors++;
}
validate41.errors = vErrors;
return errors === 0;
}
validate41.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

const schema63 = {"additionalProperties":false,"description":"Directional retraction with prior-value and retraction-notice evidence.","properties":{"current_evidence_ids":{"items":{"format":"uuid","type":"string"},"maxItems":50,"minItems":1,"title":"Current Evidence Ids","type":"array"},"current_value":{"const":null,"default":null,"title":"Current Value","type":"null"},"kind":{"const":"retraction","title":"Kind","type":"string"},"period":{"anyOf":[{"maxLength":100,"type":"string"},{"type":"null"}],"default":null,"title":"Period"},"previous_evidence_ids":{"items":{"format":"uuid","type":"string"},"maxItems":50,"minItems":1,"title":"Previous Evidence Ids","type":"array"},"previous_value":{"discriminator":{"mapping":{"decimal":"#/$defs/DecimalFactValue","text":"#/$defs/TextFactValue"},"propertyName":"kind"},"oneOf":[{"$ref":"#/$defs/TextFactValue"},{"$ref":"#/$defs/DecimalFactValue"}],"title":"Previous Value"},"slot":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Slot","type":"string"},"unit":{"anyOf":[{"maxLength":50,"type":"string"},{"type":"null"}],"default":null,"title":"Unit"}},"required":["kind","slot","previous_value","previous_evidence_ids","current_evidence_ids"],"title":"RetractionCorrection","type":"object"};

function validate43(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}, isAllOfVariant = 0}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate43.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.kind === undefined){
const err0 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "kind"},message:"must have required property '"+"kind"+"'"};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
if(data.slot === undefined){
const err1 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "slot"},message:"must have required property '"+"slot"+"'"};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
if(data.previous_value === undefined){
const err2 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "previous_value"},message:"must have required property '"+"previous_value"+"'"};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
if(data.previous_evidence_ids === undefined){
const err3 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "previous_evidence_ids"},message:"must have required property '"+"previous_evidence_ids"+"'"};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
}
if(data.current_evidence_ids === undefined){
const err4 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "current_evidence_ids"},message:"must have required property '"+"current_evidence_ids"+"'"};
if(vErrors === null){
vErrors = [err4];
}
else {
vErrors.push(err4);
}
errors++;
}
for(const key0 in data){
if(!((((((((key0 === "current_evidence_ids") || (key0 === "current_value")) || (key0 === "kind")) || (key0 === "period")) || (key0 === "previous_evidence_ids")) || (key0 === "previous_value")) || (key0 === "slot")) || (key0 === "unit"))){
const err5 = {instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err5];
}
else {
vErrors.push(err5);
}
errors++;
}
}
if(data.current_evidence_ids !== undefined){
let data0 = data.current_evidence_ids;
if(Array.isArray(data0)){
if(data0.length > 50){
const err6 = {instancePath:instancePath+"/current_evidence_ids",schemaPath:"#/properties/current_evidence_ids/maxItems",keyword:"maxItems",params:{limit: 50},message:"must NOT have more than 50 items"};
if(vErrors === null){
vErrors = [err6];
}
else {
vErrors.push(err6);
}
errors++;
}
if(data0.length < 1){
const err7 = {instancePath:instancePath+"/current_evidence_ids",schemaPath:"#/properties/current_evidence_ids/minItems",keyword:"minItems",params:{limit: 1},message:"must NOT have fewer than 1 items"};
if(vErrors === null){
vErrors = [err7];
}
else {
vErrors.push(err7);
}
errors++;
}
const len0 = data0.length;
for(let i0=0; i0<len0; i0++){
let data1 = data0[i0];
if(typeof data1 === "string"){
if(!(formats2.test(data1))){
const err8 = {instancePath:instancePath+"/current_evidence_ids/" + i0,schemaPath:"#/properties/current_evidence_ids/items/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err8];
}
else {
vErrors.push(err8);
}
errors++;
}
}
else {
const err9 = {instancePath:instancePath+"/current_evidence_ids/" + i0,schemaPath:"#/properties/current_evidence_ids/items/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err9];
}
else {
vErrors.push(err9);
}
errors++;
}
}
}
else {
const err10 = {instancePath:instancePath+"/current_evidence_ids",schemaPath:"#/properties/current_evidence_ids/type",keyword:"type",params:{type: "array"},message:"must be array"};
if(vErrors === null){
vErrors = [err10];
}
else {
vErrors.push(err10);
}
errors++;
}
}
if(data.current_value !== undefined){
let data2 = data.current_value;
if(data2 !== null){
const err11 = {instancePath:instancePath+"/current_value",schemaPath:"#/properties/current_value/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err11];
}
else {
vErrors.push(err11);
}
errors++;
}
if(null !== data2){
const err12 = {instancePath:instancePath+"/current_value",schemaPath:"#/properties/current_value/const",keyword:"const",params:{allowedValue: schema63.properties.current_value.const},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err12];
}
else {
vErrors.push(err12);
}
errors++;
}
}
if(data.kind !== undefined){
let data3 = data.kind;
if(typeof data3 !== "string"){
const err13 = {instancePath:instancePath+"/kind",schemaPath:"#/properties/kind/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err13];
}
else {
vErrors.push(err13);
}
errors++;
}
if("retraction" !== data3){
const err14 = {instancePath:instancePath+"/kind",schemaPath:"#/properties/kind/const",keyword:"const",params:{allowedValue: "retraction"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err14];
}
else {
vErrors.push(err14);
}
errors++;
}
}
if(data.period !== undefined){
let data4 = data.period;
const _errs11 = errors;
let valid3 = false;
const _errs12 = errors;
if(typeof data4 === "string"){
if(func3(data4) > 100){
const err15 = {instancePath:instancePath+"/period",schemaPath:"#/properties/period/anyOf/0/maxLength",keyword:"maxLength",params:{limit: 100},message:"must NOT have more than 100 characters"};
if(vErrors === null){
vErrors = [err15];
}
else {
vErrors.push(err15);
}
errors++;
}
}
else {
const err16 = {instancePath:instancePath+"/period",schemaPath:"#/properties/period/anyOf/0/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err16];
}
else {
vErrors.push(err16);
}
errors++;
}
var _valid0 = _errs12 === errors;
valid3 = valid3 || _valid0;
const _errs14 = errors;
if(data4 !== null){
const err17 = {instancePath:instancePath+"/period",schemaPath:"#/properties/period/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err17];
}
else {
vErrors.push(err17);
}
errors++;
}
var _valid0 = _errs14 === errors;
valid3 = valid3 || _valid0;
if(!valid3){
const err18 = {instancePath:instancePath+"/period",schemaPath:"#/properties/period/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err18];
}
else {
vErrors.push(err18);
}
errors++;
}
else {
errors = _errs11;
if(vErrors !== null){
if(_errs11){
vErrors.length = _errs11;
}
else {
vErrors = null;
}
}
}
}
if(data.previous_evidence_ids !== undefined){
let data5 = data.previous_evidence_ids;
if(Array.isArray(data5)){
if(data5.length > 50){
const err19 = {instancePath:instancePath+"/previous_evidence_ids",schemaPath:"#/properties/previous_evidence_ids/maxItems",keyword:"maxItems",params:{limit: 50},message:"must NOT have more than 50 items"};
if(vErrors === null){
vErrors = [err19];
}
else {
vErrors.push(err19);
}
errors++;
}
if(data5.length < 1){
const err20 = {instancePath:instancePath+"/previous_evidence_ids",schemaPath:"#/properties/previous_evidence_ids/minItems",keyword:"minItems",params:{limit: 1},message:"must NOT have fewer than 1 items"};
if(vErrors === null){
vErrors = [err20];
}
else {
vErrors.push(err20);
}
errors++;
}
const len1 = data5.length;
for(let i1=0; i1<len1; i1++){
let data6 = data5[i1];
if(typeof data6 === "string"){
if(!(formats2.test(data6))){
const err21 = {instancePath:instancePath+"/previous_evidence_ids/" + i1,schemaPath:"#/properties/previous_evidence_ids/items/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err21];
}
else {
vErrors.push(err21);
}
errors++;
}
}
else {
const err22 = {instancePath:instancePath+"/previous_evidence_ids/" + i1,schemaPath:"#/properties/previous_evidence_ids/items/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err22];
}
else {
vErrors.push(err22);
}
errors++;
}
}
}
else {
const err23 = {instancePath:instancePath+"/previous_evidence_ids",schemaPath:"#/properties/previous_evidence_ids/type",keyword:"type",params:{type: "array"},message:"must be array"};
if(vErrors === null){
vErrors = [err23];
}
else {
vErrors.push(err23);
}
errors++;
}
}
if(data.previous_value !== undefined){
let data7 = data.previous_value;
if(data7 && typeof data7 == "object" && !Array.isArray(data7)){
const tag0 = data7.kind;
if(typeof tag0 == "string"){
if(tag0 === "text"){
if(data7 && typeof data7 == "object" && !Array.isArray(data7)){
if(data7.kind === undefined){
const err24 = {instancePath:instancePath+"/previous_value",schemaPath:"#/$defs/TextFactValue/required",keyword:"required",params:{missingProperty: "kind"},message:"must have required property '"+"kind"+"'"};
if(vErrors === null){
vErrors = [err24];
}
else {
vErrors.push(err24);
}
errors++;
}
if(data7.value === undefined){
const err25 = {instancePath:instancePath+"/previous_value",schemaPath:"#/$defs/TextFactValue/required",keyword:"required",params:{missingProperty: "value"},message:"must have required property '"+"value"+"'"};
if(vErrors === null){
vErrors = [err25];
}
else {
vErrors.push(err25);
}
errors++;
}
for(const key1 in data7){
if(!((key1 === "kind") || (key1 === "value"))){
const err26 = {instancePath:instancePath+"/previous_value",schemaPath:"#/$defs/TextFactValue/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key1},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err26];
}
else {
vErrors.push(err26);
}
errors++;
}
}
if(data7.kind !== undefined){
let data8 = data7.kind;
if(typeof data8 !== "string"){
const err27 = {instancePath:instancePath+"/previous_value/kind",schemaPath:"#/$defs/TextFactValue/properties/kind/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err27];
}
else {
vErrors.push(err27);
}
errors++;
}
if("text" !== data8){
const err28 = {instancePath:instancePath+"/previous_value/kind",schemaPath:"#/$defs/TextFactValue/properties/kind/const",keyword:"const",params:{allowedValue: "text"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err28];
}
else {
vErrors.push(err28);
}
errors++;
}
}
if(data7.value !== undefined){
let data9 = data7.value;
if(typeof data9 === "string"){
if(func3(data9) > 500){
const err29 = {instancePath:instancePath+"/previous_value/value",schemaPath:"#/$defs/TextFactValue/properties/value/maxLength",keyword:"maxLength",params:{limit: 500},message:"must NOT have more than 500 characters"};
if(vErrors === null){
vErrors = [err29];
}
else {
vErrors.push(err29);
}
errors++;
}
if(func3(data9) < 1){
const err30 = {instancePath:instancePath+"/previous_value/value",schemaPath:"#/$defs/TextFactValue/properties/value/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err30];
}
else {
vErrors.push(err30);
}
errors++;
}
}
else {
const err31 = {instancePath:instancePath+"/previous_value/value",schemaPath:"#/$defs/TextFactValue/properties/value/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err31];
}
else {
vErrors.push(err31);
}
errors++;
}
}
}
else {
const err32 = {instancePath:instancePath+"/previous_value",schemaPath:"#/$defs/TextFactValue/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err32];
}
else {
vErrors.push(err32);
}
errors++;
}
var props0 = true;
}
else if(tag0 === "decimal"){
if(data7 && typeof data7 == "object" && !Array.isArray(data7)){
if(data7.kind === undefined){
const err33 = {instancePath:instancePath+"/previous_value",schemaPath:"#/$defs/DecimalFactValue/required",keyword:"required",params:{missingProperty: "kind"},message:"must have required property '"+"kind"+"'"};
if(vErrors === null){
vErrors = [err33];
}
else {
vErrors.push(err33);
}
errors++;
}
if(data7.value === undefined){
const err34 = {instancePath:instancePath+"/previous_value",schemaPath:"#/$defs/DecimalFactValue/required",keyword:"required",params:{missingProperty: "value"},message:"must have required property '"+"value"+"'"};
if(vErrors === null){
vErrors = [err34];
}
else {
vErrors.push(err34);
}
errors++;
}
for(const key2 in data7){
if(!((key2 === "kind") || (key2 === "value"))){
const err35 = {instancePath:instancePath+"/previous_value",schemaPath:"#/$defs/DecimalFactValue/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key2},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err35];
}
else {
vErrors.push(err35);
}
errors++;
}
}
if(data7.kind !== undefined){
let data10 = data7.kind;
if(typeof data10 !== "string"){
const err36 = {instancePath:instancePath+"/previous_value/kind",schemaPath:"#/$defs/DecimalFactValue/properties/kind/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err36];
}
else {
vErrors.push(err36);
}
errors++;
}
if("decimal" !== data10){
const err37 = {instancePath:instancePath+"/previous_value/kind",schemaPath:"#/$defs/DecimalFactValue/properties/kind/const",keyword:"const",params:{allowedValue: "decimal"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err37];
}
else {
vErrors.push(err37);
}
errors++;
}
}
if(data7.value !== undefined){
let data11 = data7.value;
if(typeof data11 === "string"){
if(!pattern14.test(data11)){
const err38 = {instancePath:instancePath+"/previous_value/value",schemaPath:"#/$defs/DecimalFactValue/properties/value/pattern",keyword:"pattern",params:{pattern: "^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$"},message:"must match pattern \""+"^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$"+"\""};
if(vErrors === null){
vErrors = [err38];
}
else {
vErrors.push(err38);
}
errors++;
}
}
else {
const err39 = {instancePath:instancePath+"/previous_value/value",schemaPath:"#/$defs/DecimalFactValue/properties/value/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err39];
}
else {
vErrors.push(err39);
}
errors++;
}
}
}
else {
const err40 = {instancePath:instancePath+"/previous_value",schemaPath:"#/$defs/DecimalFactValue/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err40];
}
else {
vErrors.push(err40);
}
errors++;
}
if(props0 !== true){
props0 = true;
}
}
else {
const err41 = {instancePath:instancePath+"/previous_value",schemaPath:"#/properties/previous_value/discriminator",keyword:"discriminator",params:{error: "mapping", tag: "kind", tagValue: tag0},message:"value of tag \"kind\" must be in oneOf or anyOf"};
if(vErrors === null){
vErrors = [err41];
}
else {
vErrors.push(err41);
}
errors++;
}
}
else {
const err42 = {instancePath:instancePath+"/previous_value",schemaPath:"#/properties/previous_value/discriminator",keyword:"discriminator",params:{error: "tag", tag: "kind", tagValue: tag0},message:"tag \"kind\" must be string"};
if(vErrors === null){
vErrors = [err42];
}
else {
vErrors.push(err42);
}
errors++;
}
}
}
if(data.slot !== undefined){
let data12 = data.slot;
if(typeof data12 === "string"){
if(!pattern4.test(data12)){
const err43 = {instancePath:instancePath+"/slot",schemaPath:"#/properties/slot/pattern",keyword:"pattern",params:{pattern: "^[a-z][a-z0-9_.-]{1,99}$"},message:"must match pattern \""+"^[a-z][a-z0-9_.-]{1,99}$"+"\""};
if(vErrors === null){
vErrors = [err43];
}
else {
vErrors.push(err43);
}
errors++;
}
}
else {
const err44 = {instancePath:instancePath+"/slot",schemaPath:"#/properties/slot/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err44];
}
else {
vErrors.push(err44);
}
errors++;
}
}
if(data.unit !== undefined){
let data13 = data.unit;
const _errs41 = errors;
let valid13 = false;
const _errs42 = errors;
if(typeof data13 === "string"){
if(func3(data13) > 50){
const err45 = {instancePath:instancePath+"/unit",schemaPath:"#/properties/unit/anyOf/0/maxLength",keyword:"maxLength",params:{limit: 50},message:"must NOT have more than 50 characters"};
if(vErrors === null){
vErrors = [err45];
}
else {
vErrors.push(err45);
}
errors++;
}
}
else {
const err46 = {instancePath:instancePath+"/unit",schemaPath:"#/properties/unit/anyOf/0/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err46];
}
else {
vErrors.push(err46);
}
errors++;
}
var _valid1 = _errs42 === errors;
valid13 = valid13 || _valid1;
const _errs44 = errors;
if(data13 !== null){
const err47 = {instancePath:instancePath+"/unit",schemaPath:"#/properties/unit/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err47];
}
else {
vErrors.push(err47);
}
errors++;
}
var _valid1 = _errs44 === errors;
valid13 = valid13 || _valid1;
if(!valid13){
const err48 = {instancePath:instancePath+"/unit",schemaPath:"#/properties/unit/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err48];
}
else {
vErrors.push(err48);
}
errors++;
}
else {
errors = _errs41;
if(vErrors !== null){
if(_errs41){
vErrors.length = _errs41;
}
else {
vErrors = null;
}
}
}
}
}
else {
const err49 = {instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err49];
}
else {
vErrors.push(err49);
}
errors++;
}
validate43.errors = vErrors;
return errors === 0;
}
validate43.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

const schema66 = {"additionalProperties":false,"description":"R1 1.2 evidence metadata with complete provider lineage.","properties":{"content_sha256":{"pattern":"^[a-f0-9]{64}$","title":"Content Sha256","type":"string"},"correction_relation":{"anyOf":[{"discriminator":{"mapping":{"replacement":"#/$defs/ReplacementEvidenceRelation","retraction":"#/$defs/RetractionEvidenceRelation"},"propertyName":"kind"},"oneOf":[{"$ref":"#/$defs/ReplacementEvidenceRelation"},{"$ref":"#/$defs/RetractionEvidenceRelation"}]},{"type":"null"}],"default":null,"title":"Correction Relation"},"event_time":{"format":"date-time","title":"Event Time","type":"string"},"evidence_id":{"format":"uuid","title":"Evidence Id","type":"string"},"fetched_at":{"format":"date-time","title":"Fetched At","type":"string"},"first_seen_at":{"format":"date-time","title":"First Seen At","type":"string"},"license_id":{"maxLength":100,"minLength":1,"title":"License Id","type":"string"},"provenance":{"$ref":"#/$defs/SourceProvenanceV1_2"},"published_at":{"format":"date-time","title":"Published At","type":"string"},"revision_known_at":{"format":"date-time","title":"Revision Known At","type":"string"},"rights_snapshot_hash":{"pattern":"^[a-f0-9]{64}$","title":"Rights Snapshot Hash","type":"string"},"source_id":{"maxLength":100,"minLength":1,"title":"Source Id","type":"string"},"source_role":{"$ref":"#/$defs/SourceRole"},"source_url":{"format":"uri","maxLength":2083,"minLength":1,"title":"Source Url","type":"string"},"title":{"maxLength":500,"minLength":1,"title":"Title","type":"string"}},"required":["evidence_id","source_id","source_role","source_url","title","published_at","first_seen_at","fetched_at","revision_known_at","content_sha256","license_id","rights_snapshot_hash","event_time","provenance"],"title":"EventEvidenceSourceV1_2","type":"object","x-runtime-invariants":["published_at <= first_seen_at <= fetched_at <= revision_known_at"]};
const schema67 = {"additionalProperties":false,"description":"A current evidence record explicitly replaces a prior record.","properties":{"kind":{"const":"replacement","title":"Kind","type":"string"},"prior_evidence_id":{"format":"uuid","title":"Prior Evidence Id","type":"string"}},"required":["kind","prior_evidence_id"],"title":"ReplacementEvidenceRelation","type":"object"};
const schema68 = {"additionalProperties":false,"description":"A current evidence record explicitly retracts a prior record.","properties":{"kind":{"const":"retraction","title":"Kind","type":"string"},"prior_evidence_id":{"format":"uuid","title":"Prior Evidence Id","type":"string"}},"required":["kind","prior_evidence_id"],"title":"RetractionEvidenceRelation","type":"object"};
const schema76 = {"description":"Permitted public source roles.","enum":["statutory","professional_market","professional_information","discovery"],"title":"SourceRole","type":"string"};
const pattern20 = new RegExp("^[a-f0-9]{64}$", "u");
const formats60 = require("./public-v12-formats.generated.cjs").uri;
const schema69 = {"additionalProperties":false,"allOf":[{"if":{"properties":{"runtime_mode":{"const":"demo_replay"}}},"then":{"properties":{"access_channel":{"const":"demo_replay"},"quality_tier":{"const":"demo_replay"},"release_tier":{"const":"dev_only"},"rights_status":{"const":"not_applicable"}}}},{"if":{"properties":{"runtime_mode":{"const":"public_static"}}},"then":{"properties":{"access_channel":{"const":"demo_replay"},"quality_tier":{"const":"demo_replay"},"release_tier":{"const":"dev_only"},"rights_status":{"const":"not_applicable"}}}},{"if":{"properties":{"runtime_mode":{"const":"dev_live"}}},"then":{"properties":{"access_channel":{"enum":["akshare","official_http","baostock_sdk"]},"quality_tier":{"enum":["free_official","free_structured","dev_web"]},"release_tier":{"const":"dev_only"},"rights_status":{"const":"unverified"}}}},{"if":{"properties":{"runtime_mode":{"const":"authorized"}}},"then":{"properties":{"access_channel":{"const":"authorized_api"},"quality_tier":{"const":"authorized"},"release_tier":{"const":"authorized"},"rights_status":{"const":"approved"}}}}],"description":"R1 1.2 lineage with explicit quality and evidence-role dimensions.","properties":{"access_channel":{"$ref":"#/$defs/SourceAccessChannelV1_2"},"adapter_name":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Adapter Name","type":"string"},"adapter_version":{"maxLength":100,"minLength":1,"title":"Adapter Version","type":"string"},"endpoint_name":{"pattern":"^[a-z][a-z0-9_.-]{1,199}$","title":"Endpoint Name","type":"string"},"evidence_role":{"$ref":"#/$defs/SourceEvidenceRole"},"provider":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Provider","type":"string"},"quality_tier":{"$ref":"#/$defs/SourceQualityTier"},"release_tier":{"$ref":"#/$defs/SourceReleaseTier"},"rights_status":{"$ref":"#/$defs/SourceRightsStatusV1_2"},"runtime_mode":{"$ref":"#/$defs/SourceRuntimeModeV1_2"},"schema_version":{"const":"1.2","default":"1.2","title":"Schema Version","type":"string"},"source_identifier":{"maxLength":500,"minLength":1,"title":"Source Identifier","type":"string"}},"required":["schema_version","provider","access_channel","adapter_name","adapter_version","endpoint_name","source_identifier","runtime_mode","release_tier","rights_status","quality_tier","evidence_role"],"title":"SourceProvenanceV1_2","type":"object","x-runtime-invariants":["runtime_mode constrains channel, release tier, rights status, and quality","quality_tier and evidence_role remain independent dimensions"]};
const schema70 = {"description":"R1 1.2 access channels used by replay, free and authorized adapters.","enum":["demo_replay","akshare","official_http","baostock_sdk","authorized_api"],"title":"SourceAccessChannelV1_2","type":"string"};
const schema71 = {"description":"How one source may support a fact without changing its source identity.","enum":["authoritative","structured_helper","discovery","cross_check"],"title":"SourceEvidenceRole","type":"string"};
const schema72 = {"description":"Coarse quality origin kept separate from evidence authority and rights.","enum":["demo_replay","free_official","free_structured","dev_web","authorized"],"title":"SourceQualityTier","type":"string"};
const schema73 = {"description":"Whether a source result may leave development-only surfaces.","enum":["dev_only","authorized"],"title":"SourceReleaseTier","type":"string"};
const schema74 = {"description":"R1 1.2 public rights state, including data with no source-rights claim.","enum":["unverified","approved","blocked","not_applicable"],"title":"SourceRightsStatusV1_2","type":"string"};

function validate46(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}, isAllOfVariant = 0}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate46.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
const _errs2 = errors;
let valid1 = true;
const _errs3 = errors;
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.runtime_mode !== undefined){
if("demo_replay" !== data.runtime_mode){
const err0 = {};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
}
}
var _valid0 = _errs3 === errors;
errors = _errs2;
if(vErrors !== null){
if(_errs2){
vErrors.length = _errs2;
}
else {
vErrors = null;
}
}
if(_valid0){
const _errs5 = errors;
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.access_channel !== undefined){
if("demo_replay" !== data.access_channel){
const err1 = {instancePath:instancePath+"/access_channel",schemaPath:"#/allOf/0/then/properties/access_channel/const",keyword:"const",params:{allowedValue: "demo_replay"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
}
if(data.quality_tier !== undefined){
if("demo_replay" !== data.quality_tier){
const err2 = {instancePath:instancePath+"/quality_tier",schemaPath:"#/allOf/0/then/properties/quality_tier/const",keyword:"const",params:{allowedValue: "demo_replay"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
}
if(data.release_tier !== undefined){
if("dev_only" !== data.release_tier){
const err3 = {instancePath:instancePath+"/release_tier",schemaPath:"#/allOf/0/then/properties/release_tier/const",keyword:"const",params:{allowedValue: "dev_only"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
}
}
if(data.rights_status !== undefined){
if("not_applicable" !== data.rights_status){
const err4 = {instancePath:instancePath+"/rights_status",schemaPath:"#/allOf/0/then/properties/rights_status/const",keyword:"const",params:{allowedValue: "not_applicable"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err4];
}
else {
vErrors.push(err4);
}
errors++;
}
}
}
var _valid0 = _errs5 === errors;
valid1 = _valid0;
if(valid1){
var props0 = {};
props0.access_channel = true;
props0.quality_tier = true;
props0.release_tier = true;
props0.rights_status = true;
props0.runtime_mode = true;
}
}
if(!valid1){
const err5 = {instancePath,schemaPath:"#/allOf/0/if",keyword:"if",params:{failingKeyword: "then"},message:"must match \"then\" schema"};
if(vErrors === null){
vErrors = [err5];
}
else {
vErrors.push(err5);
}
errors++;
}
const _errs11 = errors;
let valid4 = true;
const _errs12 = errors;
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.runtime_mode !== undefined){
if("public_static" !== data.runtime_mode){
const err6 = {};
if(vErrors === null){
vErrors = [err6];
}
else {
vErrors.push(err6);
}
errors++;
}
}
}
var _valid1 = _errs12 === errors;
errors = _errs11;
if(vErrors !== null){
if(_errs11){
vErrors.length = _errs11;
}
else {
vErrors = null;
}
}
if(_valid1){
const _errs14 = errors;
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.access_channel !== undefined){
if("demo_replay" !== data.access_channel){
const err7 = {instancePath:instancePath+"/access_channel",schemaPath:"#/allOf/1/then/properties/access_channel/const",keyword:"const",params:{allowedValue: "demo_replay"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err7];
}
else {
vErrors.push(err7);
}
errors++;
}
}
if(data.quality_tier !== undefined){
if("demo_replay" !== data.quality_tier){
const err8 = {instancePath:instancePath+"/quality_tier",schemaPath:"#/allOf/1/then/properties/quality_tier/const",keyword:"const",params:{allowedValue: "demo_replay"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err8];
}
else {
vErrors.push(err8);
}
errors++;
}
}
if(data.release_tier !== undefined){
if("dev_only" !== data.release_tier){
const err9 = {instancePath:instancePath+"/release_tier",schemaPath:"#/allOf/1/then/properties/release_tier/const",keyword:"const",params:{allowedValue: "dev_only"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err9];
}
else {
vErrors.push(err9);
}
errors++;
}
}
if(data.rights_status !== undefined){
if("not_applicable" !== data.rights_status){
const err10 = {instancePath:instancePath+"/rights_status",schemaPath:"#/allOf/1/then/properties/rights_status/const",keyword:"const",params:{allowedValue: "not_applicable"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err10];
}
else {
vErrors.push(err10);
}
errors++;
}
}
}
var _valid1 = _errs14 === errors;
valid4 = _valid1;
if(valid4){
var props1 = {};
props1.access_channel = true;
props1.quality_tier = true;
props1.release_tier = true;
props1.rights_status = true;
props1.runtime_mode = true;
}
}
if(!valid4){
const err11 = {instancePath,schemaPath:"#/allOf/1/if",keyword:"if",params:{failingKeyword: "then"},message:"must match \"then\" schema"};
if(vErrors === null){
vErrors = [err11];
}
else {
vErrors.push(err11);
}
errors++;
}
if(props0 !== true && props1 !== undefined){
if(props1 === true){
props0 = true;
}
else {
props0 = props0 || {};
Object.assign(props0, props1);
}
}
const _errs20 = errors;
let valid7 = true;
const _errs21 = errors;
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.runtime_mode !== undefined){
if("dev_live" !== data.runtime_mode){
const err12 = {};
if(vErrors === null){
vErrors = [err12];
}
else {
vErrors.push(err12);
}
errors++;
}
}
}
var _valid2 = _errs21 === errors;
errors = _errs20;
if(vErrors !== null){
if(_errs20){
vErrors.length = _errs20;
}
else {
vErrors = null;
}
}
if(_valid2){
const _errs23 = errors;
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.access_channel !== undefined){
let data11 = data.access_channel;
if(!(((data11 === "akshare") || (data11 === "official_http")) || (data11 === "baostock_sdk"))){
const err13 = {instancePath:instancePath+"/access_channel",schemaPath:"#/allOf/2/then/properties/access_channel/enum",keyword:"enum",params:{allowedValues: schema69.allOf[2].then.properties.access_channel.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err13];
}
else {
vErrors.push(err13);
}
errors++;
}
}
if(data.quality_tier !== undefined){
let data12 = data.quality_tier;
if(!(((data12 === "free_official") || (data12 === "free_structured")) || (data12 === "dev_web"))){
const err14 = {instancePath:instancePath+"/quality_tier",schemaPath:"#/allOf/2/then/properties/quality_tier/enum",keyword:"enum",params:{allowedValues: schema69.allOf[2].then.properties.quality_tier.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err14];
}
else {
vErrors.push(err14);
}
errors++;
}
}
if(data.release_tier !== undefined){
if("dev_only" !== data.release_tier){
const err15 = {instancePath:instancePath+"/release_tier",schemaPath:"#/allOf/2/then/properties/release_tier/const",keyword:"const",params:{allowedValue: "dev_only"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err15];
}
else {
vErrors.push(err15);
}
errors++;
}
}
if(data.rights_status !== undefined){
if("unverified" !== data.rights_status){
const err16 = {instancePath:instancePath+"/rights_status",schemaPath:"#/allOf/2/then/properties/rights_status/const",keyword:"const",params:{allowedValue: "unverified"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err16];
}
else {
vErrors.push(err16);
}
errors++;
}
}
}
var _valid2 = _errs23 === errors;
valid7 = _valid2;
if(valid7){
var props2 = {};
props2.access_channel = true;
props2.quality_tier = true;
props2.release_tier = true;
props2.rights_status = true;
props2.runtime_mode = true;
}
}
if(!valid7){
const err17 = {instancePath,schemaPath:"#/allOf/2/if",keyword:"if",params:{failingKeyword: "then"},message:"must match \"then\" schema"};
if(vErrors === null){
vErrors = [err17];
}
else {
vErrors.push(err17);
}
errors++;
}
if(props0 !== true && props2 !== undefined){
if(props2 === true){
props0 = true;
}
else {
props0 = props0 || {};
Object.assign(props0, props2);
}
}
const _errs29 = errors;
let valid10 = true;
const _errs30 = errors;
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.runtime_mode !== undefined){
if("authorized" !== data.runtime_mode){
const err18 = {};
if(vErrors === null){
vErrors = [err18];
}
else {
vErrors.push(err18);
}
errors++;
}
}
}
var _valid3 = _errs30 === errors;
errors = _errs29;
if(vErrors !== null){
if(_errs29){
vErrors.length = _errs29;
}
else {
vErrors = null;
}
}
if(_valid3){
const _errs32 = errors;
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.access_channel !== undefined){
if("authorized_api" !== data.access_channel){
const err19 = {instancePath:instancePath+"/access_channel",schemaPath:"#/allOf/3/then/properties/access_channel/const",keyword:"const",params:{allowedValue: "authorized_api"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err19];
}
else {
vErrors.push(err19);
}
errors++;
}
}
if(data.quality_tier !== undefined){
if("authorized" !== data.quality_tier){
const err20 = {instancePath:instancePath+"/quality_tier",schemaPath:"#/allOf/3/then/properties/quality_tier/const",keyword:"const",params:{allowedValue: "authorized"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err20];
}
else {
vErrors.push(err20);
}
errors++;
}
}
if(data.release_tier !== undefined){
if("authorized" !== data.release_tier){
const err21 = {instancePath:instancePath+"/release_tier",schemaPath:"#/allOf/3/then/properties/release_tier/const",keyword:"const",params:{allowedValue: "authorized"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err21];
}
else {
vErrors.push(err21);
}
errors++;
}
}
if(data.rights_status !== undefined){
if("approved" !== data.rights_status){
const err22 = {instancePath:instancePath+"/rights_status",schemaPath:"#/allOf/3/then/properties/rights_status/const",keyword:"const",params:{allowedValue: "approved"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err22];
}
else {
vErrors.push(err22);
}
errors++;
}
}
}
var _valid3 = _errs32 === errors;
valid10 = _valid3;
if(valid10){
var props3 = {};
props3.access_channel = true;
props3.quality_tier = true;
props3.release_tier = true;
props3.rights_status = true;
props3.runtime_mode = true;
}
}
if(!valid10){
const err23 = {instancePath,schemaPath:"#/allOf/3/if",keyword:"if",params:{failingKeyword: "then"},message:"must match \"then\" schema"};
if(vErrors === null){
vErrors = [err23];
}
else {
vErrors.push(err23);
}
errors++;
}
if(props0 !== true && props3 !== undefined){
if(props3 === true){
props0 = true;
}
else {
props0 = props0 || {};
Object.assign(props0, props3);
}
}
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.schema_version === undefined){
const err24 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "schema_version"},message:"must have required property '"+"schema_version"+"'"};
if(vErrors === null){
vErrors = [err24];
}
else {
vErrors.push(err24);
}
errors++;
}
if(data.provider === undefined){
const err25 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "provider"},message:"must have required property '"+"provider"+"'"};
if(vErrors === null){
vErrors = [err25];
}
else {
vErrors.push(err25);
}
errors++;
}
if(data.access_channel === undefined){
const err26 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "access_channel"},message:"must have required property '"+"access_channel"+"'"};
if(vErrors === null){
vErrors = [err26];
}
else {
vErrors.push(err26);
}
errors++;
}
if(data.adapter_name === undefined){
const err27 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "adapter_name"},message:"must have required property '"+"adapter_name"+"'"};
if(vErrors === null){
vErrors = [err27];
}
else {
vErrors.push(err27);
}
errors++;
}
if(data.adapter_version === undefined){
const err28 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "adapter_version"},message:"must have required property '"+"adapter_version"+"'"};
if(vErrors === null){
vErrors = [err28];
}
else {
vErrors.push(err28);
}
errors++;
}
if(data.endpoint_name === undefined){
const err29 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "endpoint_name"},message:"must have required property '"+"endpoint_name"+"'"};
if(vErrors === null){
vErrors = [err29];
}
else {
vErrors.push(err29);
}
errors++;
}
if(data.source_identifier === undefined){
const err30 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "source_identifier"},message:"must have required property '"+"source_identifier"+"'"};
if(vErrors === null){
vErrors = [err30];
}
else {
vErrors.push(err30);
}
errors++;
}
if(data.runtime_mode === undefined){
const err31 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "runtime_mode"},message:"must have required property '"+"runtime_mode"+"'"};
if(vErrors === null){
vErrors = [err31];
}
else {
vErrors.push(err31);
}
errors++;
}
if(data.release_tier === undefined){
const err32 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "release_tier"},message:"must have required property '"+"release_tier"+"'"};
if(vErrors === null){
vErrors = [err32];
}
else {
vErrors.push(err32);
}
errors++;
}
if(data.rights_status === undefined){
const err33 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "rights_status"},message:"must have required property '"+"rights_status"+"'"};
if(vErrors === null){
vErrors = [err33];
}
else {
vErrors.push(err33);
}
errors++;
}
if(data.quality_tier === undefined){
const err34 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "quality_tier"},message:"must have required property '"+"quality_tier"+"'"};
if(vErrors === null){
vErrors = [err34];
}
else {
vErrors.push(err34);
}
errors++;
}
if(data.evidence_role === undefined){
const err35 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "evidence_role"},message:"must have required property '"+"evidence_role"+"'"};
if(vErrors === null){
vErrors = [err35];
}
else {
vErrors.push(err35);
}
errors++;
}
for(const key0 in data){
if(!(func1.call(schema69.properties, key0))){
const err36 = {instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err36];
}
else {
vErrors.push(err36);
}
errors++;
}
}
if(data.access_channel !== undefined){
let data20 = data.access_channel;
if(typeof data20 !== "string"){
const err37 = {instancePath:instancePath+"/access_channel",schemaPath:"#/$defs/SourceAccessChannelV1_2/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err37];
}
else {
vErrors.push(err37);
}
errors++;
}
if(!(((((data20 === "demo_replay") || (data20 === "akshare")) || (data20 === "official_http")) || (data20 === "baostock_sdk")) || (data20 === "authorized_api"))){
const err38 = {instancePath:instancePath+"/access_channel",schemaPath:"#/$defs/SourceAccessChannelV1_2/enum",keyword:"enum",params:{allowedValues: schema70.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err38];
}
else {
vErrors.push(err38);
}
errors++;
}
}
if(data.adapter_name !== undefined){
let data21 = data.adapter_name;
if(typeof data21 === "string"){
if(!pattern4.test(data21)){
const err39 = {instancePath:instancePath+"/adapter_name",schemaPath:"#/properties/adapter_name/pattern",keyword:"pattern",params:{pattern: "^[a-z][a-z0-9_.-]{1,99}$"},message:"must match pattern \""+"^[a-z][a-z0-9_.-]{1,99}$"+"\""};
if(vErrors === null){
vErrors = [err39];
}
else {
vErrors.push(err39);
}
errors++;
}
}
else {
const err40 = {instancePath:instancePath+"/adapter_name",schemaPath:"#/properties/adapter_name/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err40];
}
else {
vErrors.push(err40);
}
errors++;
}
}
if(data.adapter_version !== undefined){
let data22 = data.adapter_version;
if(typeof data22 === "string"){
if(func3(data22) > 100){
const err41 = {instancePath:instancePath+"/adapter_version",schemaPath:"#/properties/adapter_version/maxLength",keyword:"maxLength",params:{limit: 100},message:"must NOT have more than 100 characters"};
if(vErrors === null){
vErrors = [err41];
}
else {
vErrors.push(err41);
}
errors++;
}
if(func3(data22) < 1){
const err42 = {instancePath:instancePath+"/adapter_version",schemaPath:"#/properties/adapter_version/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err42];
}
else {
vErrors.push(err42);
}
errors++;
}
}
else {
const err43 = {instancePath:instancePath+"/adapter_version",schemaPath:"#/properties/adapter_version/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err43];
}
else {
vErrors.push(err43);
}
errors++;
}
}
if(data.endpoint_name !== undefined){
let data23 = data.endpoint_name;
if(typeof data23 === "string"){
if(!pattern6.test(data23)){
const err44 = {instancePath:instancePath+"/endpoint_name",schemaPath:"#/properties/endpoint_name/pattern",keyword:"pattern",params:{pattern: "^[a-z][a-z0-9_.-]{1,199}$"},message:"must match pattern \""+"^[a-z][a-z0-9_.-]{1,199}$"+"\""};
if(vErrors === null){
vErrors = [err44];
}
else {
vErrors.push(err44);
}
errors++;
}
}
else {
const err45 = {instancePath:instancePath+"/endpoint_name",schemaPath:"#/properties/endpoint_name/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err45];
}
else {
vErrors.push(err45);
}
errors++;
}
}
if(data.evidence_role !== undefined){
let data24 = data.evidence_role;
if(typeof data24 !== "string"){
const err46 = {instancePath:instancePath+"/evidence_role",schemaPath:"#/$defs/SourceEvidenceRole/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err46];
}
else {
vErrors.push(err46);
}
errors++;
}
if(!((((data24 === "authoritative") || (data24 === "structured_helper")) || (data24 === "discovery")) || (data24 === "cross_check"))){
const err47 = {instancePath:instancePath+"/evidence_role",schemaPath:"#/$defs/SourceEvidenceRole/enum",keyword:"enum",params:{allowedValues: schema71.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err47];
}
else {
vErrors.push(err47);
}
errors++;
}
}
if(data.provider !== undefined){
let data25 = data.provider;
if(typeof data25 === "string"){
if(!pattern4.test(data25)){
const err48 = {instancePath:instancePath+"/provider",schemaPath:"#/properties/provider/pattern",keyword:"pattern",params:{pattern: "^[a-z][a-z0-9_.-]{1,99}$"},message:"must match pattern \""+"^[a-z][a-z0-9_.-]{1,99}$"+"\""};
if(vErrors === null){
vErrors = [err48];
}
else {
vErrors.push(err48);
}
errors++;
}
}
else {
const err49 = {instancePath:instancePath+"/provider",schemaPath:"#/properties/provider/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err49];
}
else {
vErrors.push(err49);
}
errors++;
}
}
if(data.quality_tier !== undefined){
let data26 = data.quality_tier;
if(typeof data26 !== "string"){
const err50 = {instancePath:instancePath+"/quality_tier",schemaPath:"#/$defs/SourceQualityTier/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err50];
}
else {
vErrors.push(err50);
}
errors++;
}
if(!(((((data26 === "demo_replay") || (data26 === "free_official")) || (data26 === "free_structured")) || (data26 === "dev_web")) || (data26 === "authorized"))){
const err51 = {instancePath:instancePath+"/quality_tier",schemaPath:"#/$defs/SourceQualityTier/enum",keyword:"enum",params:{allowedValues: schema72.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err51];
}
else {
vErrors.push(err51);
}
errors++;
}
}
if(data.release_tier !== undefined){
let data27 = data.release_tier;
if(typeof data27 !== "string"){
const err52 = {instancePath:instancePath+"/release_tier",schemaPath:"#/$defs/SourceReleaseTier/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err52];
}
else {
vErrors.push(err52);
}
errors++;
}
if(!((data27 === "dev_only") || (data27 === "authorized"))){
const err53 = {instancePath:instancePath+"/release_tier",schemaPath:"#/$defs/SourceReleaseTier/enum",keyword:"enum",params:{allowedValues: schema73.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err53];
}
else {
vErrors.push(err53);
}
errors++;
}
}
if(data.rights_status !== undefined){
let data28 = data.rights_status;
if(typeof data28 !== "string"){
const err54 = {instancePath:instancePath+"/rights_status",schemaPath:"#/$defs/SourceRightsStatusV1_2/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err54];
}
else {
vErrors.push(err54);
}
errors++;
}
if(!((((data28 === "unverified") || (data28 === "approved")) || (data28 === "blocked")) || (data28 === "not_applicable"))){
const err55 = {instancePath:instancePath+"/rights_status",schemaPath:"#/$defs/SourceRightsStatusV1_2/enum",keyword:"enum",params:{allowedValues: schema74.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err55];
}
else {
vErrors.push(err55);
}
errors++;
}
}
if(data.runtime_mode !== undefined){
let data29 = data.runtime_mode;
if(typeof data29 !== "string"){
const err56 = {instancePath:instancePath+"/runtime_mode",schemaPath:"#/$defs/SourceRuntimeModeV1_2/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err56];
}
else {
vErrors.push(err56);
}
errors++;
}
if(!((((data29 === "demo_replay") || (data29 === "dev_live")) || (data29 === "public_static")) || (data29 === "authorized"))){
const err57 = {instancePath:instancePath+"/runtime_mode",schemaPath:"#/$defs/SourceRuntimeModeV1_2/enum",keyword:"enum",params:{allowedValues: schema75.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err57];
}
else {
vErrors.push(err57);
}
errors++;
}
}
if(data.schema_version !== undefined){
let data30 = data.schema_version;
if(typeof data30 !== "string"){
const err58 = {instancePath:instancePath+"/schema_version",schemaPath:"#/properties/schema_version/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err58];
}
else {
vErrors.push(err58);
}
errors++;
}
if("1.2" !== data30){
const err59 = {instancePath:instancePath+"/schema_version",schemaPath:"#/properties/schema_version/const",keyword:"const",params:{allowedValue: "1.2"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err59];
}
else {
vErrors.push(err59);
}
errors++;
}
}
if(data.source_identifier !== undefined){
let data31 = data.source_identifier;
if(typeof data31 === "string"){
if(func3(data31) > 500){
const err60 = {instancePath:instancePath+"/source_identifier",schemaPath:"#/properties/source_identifier/maxLength",keyword:"maxLength",params:{limit: 500},message:"must NOT have more than 500 characters"};
if(vErrors === null){
vErrors = [err60];
}
else {
vErrors.push(err60);
}
errors++;
}
if(func3(data31) < 1){
const err61 = {instancePath:instancePath+"/source_identifier",schemaPath:"#/properties/source_identifier/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err61];
}
else {
vErrors.push(err61);
}
errors++;
}
}
else {
const err62 = {instancePath:instancePath+"/source_identifier",schemaPath:"#/properties/source_identifier/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err62];
}
else {
vErrors.push(err62);
}
errors++;
}
}
}
else {
const err63 = {instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err63];
}
else {
vErrors.push(err63);
}
errors++;
}
validate46.errors = vErrors;
return errors === 0;
}
validate46.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};


function validate45(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}, isAllOfVariant = 0}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate45.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.evidence_id === undefined){
const err0 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "evidence_id"},message:"must have required property '"+"evidence_id"+"'"};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
if(data.source_id === undefined){
const err1 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "source_id"},message:"must have required property '"+"source_id"+"'"};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
if(data.source_role === undefined){
const err2 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "source_role"},message:"must have required property '"+"source_role"+"'"};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
if(data.source_url === undefined){
const err3 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "source_url"},message:"must have required property '"+"source_url"+"'"};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
}
if(data.title === undefined){
const err4 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "title"},message:"must have required property '"+"title"+"'"};
if(vErrors === null){
vErrors = [err4];
}
else {
vErrors.push(err4);
}
errors++;
}
if(data.published_at === undefined){
const err5 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "published_at"},message:"must have required property '"+"published_at"+"'"};
if(vErrors === null){
vErrors = [err5];
}
else {
vErrors.push(err5);
}
errors++;
}
if(data.first_seen_at === undefined){
const err6 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "first_seen_at"},message:"must have required property '"+"first_seen_at"+"'"};
if(vErrors === null){
vErrors = [err6];
}
else {
vErrors.push(err6);
}
errors++;
}
if(data.fetched_at === undefined){
const err7 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "fetched_at"},message:"must have required property '"+"fetched_at"+"'"};
if(vErrors === null){
vErrors = [err7];
}
else {
vErrors.push(err7);
}
errors++;
}
if(data.revision_known_at === undefined){
const err8 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "revision_known_at"},message:"must have required property '"+"revision_known_at"+"'"};
if(vErrors === null){
vErrors = [err8];
}
else {
vErrors.push(err8);
}
errors++;
}
if(data.content_sha256 === undefined){
const err9 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "content_sha256"},message:"must have required property '"+"content_sha256"+"'"};
if(vErrors === null){
vErrors = [err9];
}
else {
vErrors.push(err9);
}
errors++;
}
if(data.license_id === undefined){
const err10 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "license_id"},message:"must have required property '"+"license_id"+"'"};
if(vErrors === null){
vErrors = [err10];
}
else {
vErrors.push(err10);
}
errors++;
}
if(data.rights_snapshot_hash === undefined){
const err11 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "rights_snapshot_hash"},message:"must have required property '"+"rights_snapshot_hash"+"'"};
if(vErrors === null){
vErrors = [err11];
}
else {
vErrors.push(err11);
}
errors++;
}
if(data.event_time === undefined){
const err12 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "event_time"},message:"must have required property '"+"event_time"+"'"};
if(vErrors === null){
vErrors = [err12];
}
else {
vErrors.push(err12);
}
errors++;
}
if(data.provenance === undefined){
const err13 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "provenance"},message:"must have required property '"+"provenance"+"'"};
if(vErrors === null){
vErrors = [err13];
}
else {
vErrors.push(err13);
}
errors++;
}
for(const key0 in data){
if(!(func1.call(schema66.properties, key0))){
const err14 = {instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err14];
}
else {
vErrors.push(err14);
}
errors++;
}
}
if(data.content_sha256 !== undefined){
let data0 = data.content_sha256;
if(typeof data0 === "string"){
if(!pattern20.test(data0)){
const err15 = {instancePath:instancePath+"/content_sha256",schemaPath:"#/properties/content_sha256/pattern",keyword:"pattern",params:{pattern: "^[a-f0-9]{64}$"},message:"must match pattern \""+"^[a-f0-9]{64}$"+"\""};
if(vErrors === null){
vErrors = [err15];
}
else {
vErrors.push(err15);
}
errors++;
}
}
else {
const err16 = {instancePath:instancePath+"/content_sha256",schemaPath:"#/properties/content_sha256/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err16];
}
else {
vErrors.push(err16);
}
errors++;
}
}
if(data.correction_relation !== undefined){
let data1 = data.correction_relation;
const _errs6 = errors;
let valid1 = false;
const _errs7 = errors;
if(data1 && typeof data1 == "object" && !Array.isArray(data1)){
const tag0 = data1.kind;
if(typeof tag0 == "string"){
if(tag0 === "replacement"){
if(data1 && typeof data1 == "object" && !Array.isArray(data1)){
if(data1.kind === undefined){
const err17 = {instancePath:instancePath+"/correction_relation",schemaPath:"#/$defs/ReplacementEvidenceRelation/required",keyword:"required",params:{missingProperty: "kind"},message:"must have required property '"+"kind"+"'"};
if(vErrors === null){
vErrors = [err17];
}
else {
vErrors.push(err17);
}
errors++;
}
if(data1.prior_evidence_id === undefined){
const err18 = {instancePath:instancePath+"/correction_relation",schemaPath:"#/$defs/ReplacementEvidenceRelation/required",keyword:"required",params:{missingProperty: "prior_evidence_id"},message:"must have required property '"+"prior_evidence_id"+"'"};
if(vErrors === null){
vErrors = [err18];
}
else {
vErrors.push(err18);
}
errors++;
}
for(const key1 in data1){
if(!((key1 === "kind") || (key1 === "prior_evidence_id"))){
const err19 = {instancePath:instancePath+"/correction_relation",schemaPath:"#/$defs/ReplacementEvidenceRelation/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key1},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err19];
}
else {
vErrors.push(err19);
}
errors++;
}
}
if(data1.kind !== undefined){
let data2 = data1.kind;
if(typeof data2 !== "string"){
const err20 = {instancePath:instancePath+"/correction_relation/kind",schemaPath:"#/$defs/ReplacementEvidenceRelation/properties/kind/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err20];
}
else {
vErrors.push(err20);
}
errors++;
}
if("replacement" !== data2){
const err21 = {instancePath:instancePath+"/correction_relation/kind",schemaPath:"#/$defs/ReplacementEvidenceRelation/properties/kind/const",keyword:"const",params:{allowedValue: "replacement"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err21];
}
else {
vErrors.push(err21);
}
errors++;
}
}
if(data1.prior_evidence_id !== undefined){
let data3 = data1.prior_evidence_id;
if(typeof data3 === "string"){
if(!(formats2.test(data3))){
const err22 = {instancePath:instancePath+"/correction_relation/prior_evidence_id",schemaPath:"#/$defs/ReplacementEvidenceRelation/properties/prior_evidence_id/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err22];
}
else {
vErrors.push(err22);
}
errors++;
}
}
else {
const err23 = {instancePath:instancePath+"/correction_relation/prior_evidence_id",schemaPath:"#/$defs/ReplacementEvidenceRelation/properties/prior_evidence_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err23];
}
else {
vErrors.push(err23);
}
errors++;
}
}
}
else {
const err24 = {instancePath:instancePath+"/correction_relation",schemaPath:"#/$defs/ReplacementEvidenceRelation/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err24];
}
else {
vErrors.push(err24);
}
errors++;
}
var props0 = true;
}
else if(tag0 === "retraction"){
if(data1 && typeof data1 == "object" && !Array.isArray(data1)){
if(data1.kind === undefined){
const err25 = {instancePath:instancePath+"/correction_relation",schemaPath:"#/$defs/RetractionEvidenceRelation/required",keyword:"required",params:{missingProperty: "kind"},message:"must have required property '"+"kind"+"'"};
if(vErrors === null){
vErrors = [err25];
}
else {
vErrors.push(err25);
}
errors++;
}
if(data1.prior_evidence_id === undefined){
const err26 = {instancePath:instancePath+"/correction_relation",schemaPath:"#/$defs/RetractionEvidenceRelation/required",keyword:"required",params:{missingProperty: "prior_evidence_id"},message:"must have required property '"+"prior_evidence_id"+"'"};
if(vErrors === null){
vErrors = [err26];
}
else {
vErrors.push(err26);
}
errors++;
}
for(const key2 in data1){
if(!((key2 === "kind") || (key2 === "prior_evidence_id"))){
const err27 = {instancePath:instancePath+"/correction_relation",schemaPath:"#/$defs/RetractionEvidenceRelation/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key2},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err27];
}
else {
vErrors.push(err27);
}
errors++;
}
}
if(data1.kind !== undefined){
let data4 = data1.kind;
if(typeof data4 !== "string"){
const err28 = {instancePath:instancePath+"/correction_relation/kind",schemaPath:"#/$defs/RetractionEvidenceRelation/properties/kind/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err28];
}
else {
vErrors.push(err28);
}
errors++;
}
if("retraction" !== data4){
const err29 = {instancePath:instancePath+"/correction_relation/kind",schemaPath:"#/$defs/RetractionEvidenceRelation/properties/kind/const",keyword:"const",params:{allowedValue: "retraction"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err29];
}
else {
vErrors.push(err29);
}
errors++;
}
}
if(data1.prior_evidence_id !== undefined){
let data5 = data1.prior_evidence_id;
if(typeof data5 === "string"){
if(!(formats2.test(data5))){
const err30 = {instancePath:instancePath+"/correction_relation/prior_evidence_id",schemaPath:"#/$defs/RetractionEvidenceRelation/properties/prior_evidence_id/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err30];
}
else {
vErrors.push(err30);
}
errors++;
}
}
else {
const err31 = {instancePath:instancePath+"/correction_relation/prior_evidence_id",schemaPath:"#/$defs/RetractionEvidenceRelation/properties/prior_evidence_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err31];
}
else {
vErrors.push(err31);
}
errors++;
}
}
}
else {
const err32 = {instancePath:instancePath+"/correction_relation",schemaPath:"#/$defs/RetractionEvidenceRelation/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err32];
}
else {
vErrors.push(err32);
}
errors++;
}
if(props0 !== true){
props0 = true;
}
}
else {
const err33 = {instancePath:instancePath+"/correction_relation",schemaPath:"#/properties/correction_relation/anyOf/0/discriminator",keyword:"discriminator",params:{error: "mapping", tag: "kind", tagValue: tag0},message:"value of tag \"kind\" must be in oneOf or anyOf"};
if(vErrors === null){
vErrors = [err33];
}
else {
vErrors.push(err33);
}
errors++;
}
}
else {
const err34 = {instancePath:instancePath+"/correction_relation",schemaPath:"#/properties/correction_relation/anyOf/0/discriminator",keyword:"discriminator",params:{error: "tag", tag: "kind", tagValue: tag0},message:"tag \"kind\" must be string"};
if(vErrors === null){
vErrors = [err34];
}
else {
vErrors.push(err34);
}
errors++;
}
}
var _valid0 = _errs7 === errors;
valid1 = valid1 || _valid0;
const _errs25 = errors;
if(data1 !== null){
const err35 = {instancePath:instancePath+"/correction_relation",schemaPath:"#/properties/correction_relation/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err35];
}
else {
vErrors.push(err35);
}
errors++;
}
var _valid0 = _errs25 === errors;
valid1 = valid1 || _valid0;
if(!valid1){
const err36 = {instancePath:instancePath+"/correction_relation",schemaPath:"#/properties/correction_relation/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err36];
}
else {
vErrors.push(err36);
}
errors++;
}
else {
errors = _errs6;
if(vErrors !== null){
if(_errs6){
vErrors.length = _errs6;
}
else {
vErrors = null;
}
}
}
}
if(data.event_time !== undefined){
let data6 = data.event_time;
if(typeof data6 === "string"){
if(!(formats0(data6))){
const err37 = {instancePath:instancePath+"/event_time",schemaPath:"#/properties/event_time/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err37];
}
else {
vErrors.push(err37);
}
errors++;
}
}
else {
const err38 = {instancePath:instancePath+"/event_time",schemaPath:"#/properties/event_time/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err38];
}
else {
vErrors.push(err38);
}
errors++;
}
}
if(data.evidence_id !== undefined){
let data7 = data.evidence_id;
if(typeof data7 === "string"){
if(!(formats2.test(data7))){
const err39 = {instancePath:instancePath+"/evidence_id",schemaPath:"#/properties/evidence_id/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err39];
}
else {
vErrors.push(err39);
}
errors++;
}
}
else {
const err40 = {instancePath:instancePath+"/evidence_id",schemaPath:"#/properties/evidence_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err40];
}
else {
vErrors.push(err40);
}
errors++;
}
}
if(data.fetched_at !== undefined){
let data8 = data.fetched_at;
if(typeof data8 === "string"){
if(!(formats0(data8))){
const err41 = {instancePath:instancePath+"/fetched_at",schemaPath:"#/properties/fetched_at/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err41];
}
else {
vErrors.push(err41);
}
errors++;
}
}
else {
const err42 = {instancePath:instancePath+"/fetched_at",schemaPath:"#/properties/fetched_at/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err42];
}
else {
vErrors.push(err42);
}
errors++;
}
}
if(data.first_seen_at !== undefined){
let data9 = data.first_seen_at;
if(typeof data9 === "string"){
if(!(formats0(data9))){
const err43 = {instancePath:instancePath+"/first_seen_at",schemaPath:"#/properties/first_seen_at/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err43];
}
else {
vErrors.push(err43);
}
errors++;
}
}
else {
const err44 = {instancePath:instancePath+"/first_seen_at",schemaPath:"#/properties/first_seen_at/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err44];
}
else {
vErrors.push(err44);
}
errors++;
}
}
if(data.license_id !== undefined){
let data10 = data.license_id;
if(typeof data10 === "string"){
if(func3(data10) > 100){
const err45 = {instancePath:instancePath+"/license_id",schemaPath:"#/properties/license_id/maxLength",keyword:"maxLength",params:{limit: 100},message:"must NOT have more than 100 characters"};
if(vErrors === null){
vErrors = [err45];
}
else {
vErrors.push(err45);
}
errors++;
}
if(func3(data10) < 1){
const err46 = {instancePath:instancePath+"/license_id",schemaPath:"#/properties/license_id/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err46];
}
else {
vErrors.push(err46);
}
errors++;
}
}
else {
const err47 = {instancePath:instancePath+"/license_id",schemaPath:"#/properties/license_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err47];
}
else {
vErrors.push(err47);
}
errors++;
}
}
if(data.provenance !== undefined){
let data11 = data.provenance;
const visitedNodes0 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate46) || new Set();
if(!visitedNodes0.has(data11)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate46, visitedNodes0);
const dataNode = data11;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes0.add(dataNode);
if(!(validate46(data11, {instancePath:instancePath+"/provenance",parentData:data,parentDataProperty:"provenance",rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate46.errors : vErrors.concat(validate46.errors);
errors = vErrors.length;
}
visitedNodes0.delete(dataNode);
}
}
if(data.published_at !== undefined){
let data12 = data.published_at;
if(typeof data12 === "string"){
if(!(formats0(data12))){
const err48 = {instancePath:instancePath+"/published_at",schemaPath:"#/properties/published_at/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err48];
}
else {
vErrors.push(err48);
}
errors++;
}
}
else {
const err49 = {instancePath:instancePath+"/published_at",schemaPath:"#/properties/published_at/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err49];
}
else {
vErrors.push(err49);
}
errors++;
}
}
if(data.revision_known_at !== undefined){
let data13 = data.revision_known_at;
if(typeof data13 === "string"){
if(!(formats0(data13))){
const err50 = {instancePath:instancePath+"/revision_known_at",schemaPath:"#/properties/revision_known_at/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err50];
}
else {
vErrors.push(err50);
}
errors++;
}
}
else {
const err51 = {instancePath:instancePath+"/revision_known_at",schemaPath:"#/properties/revision_known_at/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err51];
}
else {
vErrors.push(err51);
}
errors++;
}
}
if(data.rights_snapshot_hash !== undefined){
let data14 = data.rights_snapshot_hash;
if(typeof data14 === "string"){
if(!pattern20.test(data14)){
const err52 = {instancePath:instancePath+"/rights_snapshot_hash",schemaPath:"#/properties/rights_snapshot_hash/pattern",keyword:"pattern",params:{pattern: "^[a-f0-9]{64}$"},message:"must match pattern \""+"^[a-f0-9]{64}$"+"\""};
if(vErrors === null){
vErrors = [err52];
}
else {
vErrors.push(err52);
}
errors++;
}
}
else {
const err53 = {instancePath:instancePath+"/rights_snapshot_hash",schemaPath:"#/properties/rights_snapshot_hash/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err53];
}
else {
vErrors.push(err53);
}
errors++;
}
}
if(data.source_id !== undefined){
let data15 = data.source_id;
if(typeof data15 === "string"){
if(func3(data15) > 100){
const err54 = {instancePath:instancePath+"/source_id",schemaPath:"#/properties/source_id/maxLength",keyword:"maxLength",params:{limit: 100},message:"must NOT have more than 100 characters"};
if(vErrors === null){
vErrors = [err54];
}
else {
vErrors.push(err54);
}
errors++;
}
if(func3(data15) < 1){
const err55 = {instancePath:instancePath+"/source_id",schemaPath:"#/properties/source_id/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err55];
}
else {
vErrors.push(err55);
}
errors++;
}
}
else {
const err56 = {instancePath:instancePath+"/source_id",schemaPath:"#/properties/source_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err56];
}
else {
vErrors.push(err56);
}
errors++;
}
}
if(data.source_role !== undefined){
let data16 = data.source_role;
if(typeof data16 !== "string"){
const err57 = {instancePath:instancePath+"/source_role",schemaPath:"#/$defs/SourceRole/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err57];
}
else {
vErrors.push(err57);
}
errors++;
}
if(!((((data16 === "statutory") || (data16 === "professional_market")) || (data16 === "professional_information")) || (data16 === "discovery"))){
const err58 = {instancePath:instancePath+"/source_role",schemaPath:"#/$defs/SourceRole/enum",keyword:"enum",params:{allowedValues: schema76.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err58];
}
else {
vErrors.push(err58);
}
errors++;
}
}
if(data.source_url !== undefined){
let data17 = data.source_url;
if(typeof data17 === "string"){
if(func3(data17) > 2083){
const err59 = {instancePath:instancePath+"/source_url",schemaPath:"#/properties/source_url/maxLength",keyword:"maxLength",params:{limit: 2083},message:"must NOT have more than 2083 characters"};
if(vErrors === null){
vErrors = [err59];
}
else {
vErrors.push(err59);
}
errors++;
}
if(func3(data17) < 1){
const err60 = {instancePath:instancePath+"/source_url",schemaPath:"#/properties/source_url/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err60];
}
else {
vErrors.push(err60);
}
errors++;
}
if(!(formats60(data17))){
const err61 = {instancePath:instancePath+"/source_url",schemaPath:"#/properties/source_url/format",keyword:"format",params:{format: "uri"},message:"must match format \""+"uri"+"\""};
if(vErrors === null){
vErrors = [err61];
}
else {
vErrors.push(err61);
}
errors++;
}
}
else {
const err62 = {instancePath:instancePath+"/source_url",schemaPath:"#/properties/source_url/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err62];
}
else {
vErrors.push(err62);
}
errors++;
}
}
if(data.title !== undefined){
let data18 = data.title;
if(typeof data18 === "string"){
if(func3(data18) > 500){
const err63 = {instancePath:instancePath+"/title",schemaPath:"#/properties/title/maxLength",keyword:"maxLength",params:{limit: 500},message:"must NOT have more than 500 characters"};
if(vErrors === null){
vErrors = [err63];
}
else {
vErrors.push(err63);
}
errors++;
}
if(func3(data18) < 1){
const err64 = {instancePath:instancePath+"/title",schemaPath:"#/properties/title/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err64];
}
else {
vErrors.push(err64);
}
errors++;
}
}
else {
const err65 = {instancePath:instancePath+"/title",schemaPath:"#/properties/title/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err65];
}
else {
vErrors.push(err65);
}
errors++;
}
}
}
else {
const err66 = {instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err66];
}
else {
vErrors.push(err66);
}
errors++;
}
validate45.errors = vErrors;
return errors === 0;
}
validate45.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

const schema77 = {"additionalProperties":false,"description":"One normalized fact value with explicit evidence support.","properties":{"evidence_ids":{"items":{"format":"uuid","type":"string"},"maxItems":50,"minItems":1,"title":"Evidence Ids","type":"array"},"period":{"anyOf":[{"maxLength":100,"type":"string"},{"type":"null"}],"default":null,"title":"Period"},"slot":{"pattern":"^[a-z][a-z0-9_.-]{1,99}$","title":"Slot","type":"string"},"unit":{"anyOf":[{"maxLength":50,"type":"string"},{"type":"null"}],"default":null,"title":"Unit"},"value":{"discriminator":{"mapping":{"decimal":"#/$defs/DecimalFactValue","text":"#/$defs/TextFactValue"},"propertyName":"kind"},"oneOf":[{"$ref":"#/$defs/TextFactValue"},{"$ref":"#/$defs/DecimalFactValue"}],"title":"Value"}},"required":["slot","value","evidence_ids"],"title":"EventFactSlot","type":"object","x-runtime-invariants":["evidence_ids entries are unique"]};

function validate49(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}, isAllOfVariant = 0}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate49.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.slot === undefined){
const err0 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "slot"},message:"must have required property '"+"slot"+"'"};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
if(data.value === undefined){
const err1 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "value"},message:"must have required property '"+"value"+"'"};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
if(data.evidence_ids === undefined){
const err2 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "evidence_ids"},message:"must have required property '"+"evidence_ids"+"'"};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
for(const key0 in data){
if(!(((((key0 === "evidence_ids") || (key0 === "period")) || (key0 === "slot")) || (key0 === "unit")) || (key0 === "value"))){
const err3 = {instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
}
}
if(data.evidence_ids !== undefined){
let data0 = data.evidence_ids;
if(Array.isArray(data0)){
if(data0.length > 50){
const err4 = {instancePath:instancePath+"/evidence_ids",schemaPath:"#/properties/evidence_ids/maxItems",keyword:"maxItems",params:{limit: 50},message:"must NOT have more than 50 items"};
if(vErrors === null){
vErrors = [err4];
}
else {
vErrors.push(err4);
}
errors++;
}
if(data0.length < 1){
const err5 = {instancePath:instancePath+"/evidence_ids",schemaPath:"#/properties/evidence_ids/minItems",keyword:"minItems",params:{limit: 1},message:"must NOT have fewer than 1 items"};
if(vErrors === null){
vErrors = [err5];
}
else {
vErrors.push(err5);
}
errors++;
}
const len0 = data0.length;
for(let i0=0; i0<len0; i0++){
let data1 = data0[i0];
if(typeof data1 === "string"){
if(!(formats2.test(data1))){
const err6 = {instancePath:instancePath+"/evidence_ids/" + i0,schemaPath:"#/properties/evidence_ids/items/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err6];
}
else {
vErrors.push(err6);
}
errors++;
}
}
else {
const err7 = {instancePath:instancePath+"/evidence_ids/" + i0,schemaPath:"#/properties/evidence_ids/items/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err7];
}
else {
vErrors.push(err7);
}
errors++;
}
}
}
else {
const err8 = {instancePath:instancePath+"/evidence_ids",schemaPath:"#/properties/evidence_ids/type",keyword:"type",params:{type: "array"},message:"must be array"};
if(vErrors === null){
vErrors = [err8];
}
else {
vErrors.push(err8);
}
errors++;
}
}
if(data.period !== undefined){
let data2 = data.period;
const _errs8 = errors;
let valid3 = false;
const _errs9 = errors;
if(typeof data2 === "string"){
if(func3(data2) > 100){
const err9 = {instancePath:instancePath+"/period",schemaPath:"#/properties/period/anyOf/0/maxLength",keyword:"maxLength",params:{limit: 100},message:"must NOT have more than 100 characters"};
if(vErrors === null){
vErrors = [err9];
}
else {
vErrors.push(err9);
}
errors++;
}
}
else {
const err10 = {instancePath:instancePath+"/period",schemaPath:"#/properties/period/anyOf/0/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err10];
}
else {
vErrors.push(err10);
}
errors++;
}
var _valid0 = _errs9 === errors;
valid3 = valid3 || _valid0;
const _errs11 = errors;
if(data2 !== null){
const err11 = {instancePath:instancePath+"/period",schemaPath:"#/properties/period/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err11];
}
else {
vErrors.push(err11);
}
errors++;
}
var _valid0 = _errs11 === errors;
valid3 = valid3 || _valid0;
if(!valid3){
const err12 = {instancePath:instancePath+"/period",schemaPath:"#/properties/period/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err12];
}
else {
vErrors.push(err12);
}
errors++;
}
else {
errors = _errs8;
if(vErrors !== null){
if(_errs8){
vErrors.length = _errs8;
}
else {
vErrors = null;
}
}
}
}
if(data.slot !== undefined){
let data3 = data.slot;
if(typeof data3 === "string"){
if(!pattern4.test(data3)){
const err13 = {instancePath:instancePath+"/slot",schemaPath:"#/properties/slot/pattern",keyword:"pattern",params:{pattern: "^[a-z][a-z0-9_.-]{1,99}$"},message:"must match pattern \""+"^[a-z][a-z0-9_.-]{1,99}$"+"\""};
if(vErrors === null){
vErrors = [err13];
}
else {
vErrors.push(err13);
}
errors++;
}
}
else {
const err14 = {instancePath:instancePath+"/slot",schemaPath:"#/properties/slot/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err14];
}
else {
vErrors.push(err14);
}
errors++;
}
}
if(data.unit !== undefined){
let data4 = data.unit;
const _errs16 = errors;
let valid4 = false;
const _errs17 = errors;
if(typeof data4 === "string"){
if(func3(data4) > 50){
const err15 = {instancePath:instancePath+"/unit",schemaPath:"#/properties/unit/anyOf/0/maxLength",keyword:"maxLength",params:{limit: 50},message:"must NOT have more than 50 characters"};
if(vErrors === null){
vErrors = [err15];
}
else {
vErrors.push(err15);
}
errors++;
}
}
else {
const err16 = {instancePath:instancePath+"/unit",schemaPath:"#/properties/unit/anyOf/0/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err16];
}
else {
vErrors.push(err16);
}
errors++;
}
var _valid1 = _errs17 === errors;
valid4 = valid4 || _valid1;
const _errs19 = errors;
if(data4 !== null){
const err17 = {instancePath:instancePath+"/unit",schemaPath:"#/properties/unit/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err17];
}
else {
vErrors.push(err17);
}
errors++;
}
var _valid1 = _errs19 === errors;
valid4 = valid4 || _valid1;
if(!valid4){
const err18 = {instancePath:instancePath+"/unit",schemaPath:"#/properties/unit/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err18];
}
else {
vErrors.push(err18);
}
errors++;
}
else {
errors = _errs16;
if(vErrors !== null){
if(_errs16){
vErrors.length = _errs16;
}
else {
vErrors = null;
}
}
}
}
if(data.value !== undefined){
let data5 = data.value;
if(data5 && typeof data5 == "object" && !Array.isArray(data5)){
const tag0 = data5.kind;
if(typeof tag0 == "string"){
if(tag0 === "text"){
if(data5 && typeof data5 == "object" && !Array.isArray(data5)){
if(data5.kind === undefined){
const err19 = {instancePath:instancePath+"/value",schemaPath:"#/$defs/TextFactValue/required",keyword:"required",params:{missingProperty: "kind"},message:"must have required property '"+"kind"+"'"};
if(vErrors === null){
vErrors = [err19];
}
else {
vErrors.push(err19);
}
errors++;
}
if(data5.value === undefined){
const err20 = {instancePath:instancePath+"/value",schemaPath:"#/$defs/TextFactValue/required",keyword:"required",params:{missingProperty: "value"},message:"must have required property '"+"value"+"'"};
if(vErrors === null){
vErrors = [err20];
}
else {
vErrors.push(err20);
}
errors++;
}
for(const key1 in data5){
if(!((key1 === "kind") || (key1 === "value"))){
const err21 = {instancePath:instancePath+"/value",schemaPath:"#/$defs/TextFactValue/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key1},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err21];
}
else {
vErrors.push(err21);
}
errors++;
}
}
if(data5.kind !== undefined){
let data6 = data5.kind;
if(typeof data6 !== "string"){
const err22 = {instancePath:instancePath+"/value/kind",schemaPath:"#/$defs/TextFactValue/properties/kind/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err22];
}
else {
vErrors.push(err22);
}
errors++;
}
if("text" !== data6){
const err23 = {instancePath:instancePath+"/value/kind",schemaPath:"#/$defs/TextFactValue/properties/kind/const",keyword:"const",params:{allowedValue: "text"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err23];
}
else {
vErrors.push(err23);
}
errors++;
}
}
if(data5.value !== undefined){
let data7 = data5.value;
if(typeof data7 === "string"){
if(func3(data7) > 500){
const err24 = {instancePath:instancePath+"/value/value",schemaPath:"#/$defs/TextFactValue/properties/value/maxLength",keyword:"maxLength",params:{limit: 500},message:"must NOT have more than 500 characters"};
if(vErrors === null){
vErrors = [err24];
}
else {
vErrors.push(err24);
}
errors++;
}
if(func3(data7) < 1){
const err25 = {instancePath:instancePath+"/value/value",schemaPath:"#/$defs/TextFactValue/properties/value/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err25];
}
else {
vErrors.push(err25);
}
errors++;
}
}
else {
const err26 = {instancePath:instancePath+"/value/value",schemaPath:"#/$defs/TextFactValue/properties/value/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err26];
}
else {
vErrors.push(err26);
}
errors++;
}
}
}
else {
const err27 = {instancePath:instancePath+"/value",schemaPath:"#/$defs/TextFactValue/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err27];
}
else {
vErrors.push(err27);
}
errors++;
}
var props0 = true;
}
else if(tag0 === "decimal"){
if(data5 && typeof data5 == "object" && !Array.isArray(data5)){
if(data5.kind === undefined){
const err28 = {instancePath:instancePath+"/value",schemaPath:"#/$defs/DecimalFactValue/required",keyword:"required",params:{missingProperty: "kind"},message:"must have required property '"+"kind"+"'"};
if(vErrors === null){
vErrors = [err28];
}
else {
vErrors.push(err28);
}
errors++;
}
if(data5.value === undefined){
const err29 = {instancePath:instancePath+"/value",schemaPath:"#/$defs/DecimalFactValue/required",keyword:"required",params:{missingProperty: "value"},message:"must have required property '"+"value"+"'"};
if(vErrors === null){
vErrors = [err29];
}
else {
vErrors.push(err29);
}
errors++;
}
for(const key2 in data5){
if(!((key2 === "kind") || (key2 === "value"))){
const err30 = {instancePath:instancePath+"/value",schemaPath:"#/$defs/DecimalFactValue/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key2},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err30];
}
else {
vErrors.push(err30);
}
errors++;
}
}
if(data5.kind !== undefined){
let data8 = data5.kind;
if(typeof data8 !== "string"){
const err31 = {instancePath:instancePath+"/value/kind",schemaPath:"#/$defs/DecimalFactValue/properties/kind/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err31];
}
else {
vErrors.push(err31);
}
errors++;
}
if("decimal" !== data8){
const err32 = {instancePath:instancePath+"/value/kind",schemaPath:"#/$defs/DecimalFactValue/properties/kind/const",keyword:"const",params:{allowedValue: "decimal"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err32];
}
else {
vErrors.push(err32);
}
errors++;
}
}
if(data5.value !== undefined){
let data9 = data5.value;
if(typeof data9 === "string"){
if(!pattern14.test(data9)){
const err33 = {instancePath:instancePath+"/value/value",schemaPath:"#/$defs/DecimalFactValue/properties/value/pattern",keyword:"pattern",params:{pattern: "^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$"},message:"must match pattern \""+"^-?(?:0|[1-9][0-9]*)(?:\\.[0-9]+)?(?:[eE][+-]?[0-9]+)?$"+"\""};
if(vErrors === null){
vErrors = [err33];
}
else {
vErrors.push(err33);
}
errors++;
}
}
else {
const err34 = {instancePath:instancePath+"/value/value",schemaPath:"#/$defs/DecimalFactValue/properties/value/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err34];
}
else {
vErrors.push(err34);
}
errors++;
}
}
}
else {
const err35 = {instancePath:instancePath+"/value",schemaPath:"#/$defs/DecimalFactValue/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err35];
}
else {
vErrors.push(err35);
}
errors++;
}
if(props0 !== true){
props0 = true;
}
}
else {
const err36 = {instancePath:instancePath+"/value",schemaPath:"#/properties/value/discriminator",keyword:"discriminator",params:{error: "mapping", tag: "kind", tagValue: tag0},message:"value of tag \"kind\" must be in oneOf or anyOf"};
if(vErrors === null){
vErrors = [err36];
}
else {
vErrors.push(err36);
}
errors++;
}
}
else {
const err37 = {instancePath:instancePath+"/value",schemaPath:"#/properties/value/discriminator",keyword:"discriminator",params:{error: "tag", tag: "kind", tagValue: tag0},message:"tag \"kind\" must be string"};
if(vErrors === null){
vErrors = [err37];
}
else {
vErrors.push(err37);
}
errors++;
}
}
}
}
else {
const err38 = {instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err38];
}
else {
vErrors.push(err38);
}
errors++;
}
validate49.errors = vErrors;
return errors === 0;
}
validate49.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

const schema80 = {"additionalProperties":false,"description":"A descending, bounded page of revision identities.","properties":{"before_revision":{"anyOf":[{"minimum":1,"type":"integer"},{"type":"null"}],"default":null,"title":"Before Revision"},"has_older":{"title":"Has Older","type":"boolean"},"items":{"items":{"$ref":"#/$defs/EventRevisionTimelineItem"},"maxItems":50,"title":"Items","type":"array"},"next_before_revision":{"anyOf":[{"minimum":1,"type":"integer"},{"type":"null"}],"default":null,"title":"Next Before Revision"}},"required":["has_older"],"title":"EventRevisionTimeline","type":"object","x-runtime-invariants":["items revisions are unique and strictly descending","before_revision=null is the first page and must not be empty","continuation page items have revision < before_revision","an empty page is permitted only for before_revision=1 termination","has_older=true requires next_before_revision to equal the oldest returned revision and to be greater than 1","has_older=false requires next_before_revision=null"]};
const schema81 = {"additionalProperties":false,"description":"One immutable revision identity safe to expose in the evidence timeline.","properties":{"audit_id":{"format":"uuid","title":"Audit Id","type":"string"},"change_type":{"$ref":"#/$defs/BriefingChangeType"},"extraction_version":{"maxLength":100,"minLength":1,"title":"Extraction Version","type":"string"},"revision":{"minimum":1,"title":"Revision","type":"integer"},"revision_known_at":{"format":"date-time","title":"Revision Known At","type":"string"},"ruleset_version":{"maxLength":100,"minLength":1,"title":"Ruleset Version","type":"string"},"supersedes_revision":{"anyOf":[{"minimum":1,"type":"integer"},{"type":"null"}],"default":null,"title":"Supersedes Revision"}},"required":["revision","change_type","revision_known_at","extraction_version","ruleset_version","audit_id"],"title":"EventRevisionTimelineItem","type":"object"};

function validate52(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}, isAllOfVariant = 0}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate52.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.revision === undefined){
const err0 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "revision"},message:"must have required property '"+"revision"+"'"};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
if(data.change_type === undefined){
const err1 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "change_type"},message:"must have required property '"+"change_type"+"'"};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
if(data.revision_known_at === undefined){
const err2 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "revision_known_at"},message:"must have required property '"+"revision_known_at"+"'"};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
if(data.extraction_version === undefined){
const err3 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "extraction_version"},message:"must have required property '"+"extraction_version"+"'"};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
}
if(data.ruleset_version === undefined){
const err4 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "ruleset_version"},message:"must have required property '"+"ruleset_version"+"'"};
if(vErrors === null){
vErrors = [err4];
}
else {
vErrors.push(err4);
}
errors++;
}
if(data.audit_id === undefined){
const err5 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "audit_id"},message:"must have required property '"+"audit_id"+"'"};
if(vErrors === null){
vErrors = [err5];
}
else {
vErrors.push(err5);
}
errors++;
}
for(const key0 in data){
if(!(((((((key0 === "audit_id") || (key0 === "change_type")) || (key0 === "extraction_version")) || (key0 === "revision")) || (key0 === "revision_known_at")) || (key0 === "ruleset_version")) || (key0 === "supersedes_revision"))){
const err6 = {instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err6];
}
else {
vErrors.push(err6);
}
errors++;
}
}
if(data.audit_id !== undefined){
let data0 = data.audit_id;
if(typeof data0 === "string"){
if(!(formats2.test(data0))){
const err7 = {instancePath:instancePath+"/audit_id",schemaPath:"#/properties/audit_id/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err7];
}
else {
vErrors.push(err7);
}
errors++;
}
}
else {
const err8 = {instancePath:instancePath+"/audit_id",schemaPath:"#/properties/audit_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err8];
}
else {
vErrors.push(err8);
}
errors++;
}
}
if(data.change_type !== undefined){
let data1 = data.change_type;
if(typeof data1 !== "string"){
const err9 = {instancePath:instancePath+"/change_type",schemaPath:"#/$defs/BriefingChangeType/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err9];
}
else {
vErrors.push(err9);
}
errors++;
}
if(!((((data1 === "new") || (data1 === "update")) || (data1 === "correction")) || (data1 === "contradiction"))){
const err10 = {instancePath:instancePath+"/change_type",schemaPath:"#/$defs/BriefingChangeType/enum",keyword:"enum",params:{allowedValues: schema57.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err10];
}
else {
vErrors.push(err10);
}
errors++;
}
}
if(data.extraction_version !== undefined){
let data2 = data.extraction_version;
if(typeof data2 === "string"){
if(func3(data2) > 100){
const err11 = {instancePath:instancePath+"/extraction_version",schemaPath:"#/properties/extraction_version/maxLength",keyword:"maxLength",params:{limit: 100},message:"must NOT have more than 100 characters"};
if(vErrors === null){
vErrors = [err11];
}
else {
vErrors.push(err11);
}
errors++;
}
if(func3(data2) < 1){
const err12 = {instancePath:instancePath+"/extraction_version",schemaPath:"#/properties/extraction_version/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err12];
}
else {
vErrors.push(err12);
}
errors++;
}
}
else {
const err13 = {instancePath:instancePath+"/extraction_version",schemaPath:"#/properties/extraction_version/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err13];
}
else {
vErrors.push(err13);
}
errors++;
}
}
if(data.revision !== undefined){
let data3 = data.revision;
if(!(((typeof data3 == "number") && (!(data3 % 1) && !isNaN(data3))) && (isFinite(data3)))){
const err14 = {instancePath:instancePath+"/revision",schemaPath:"#/properties/revision/type",keyword:"type",params:{type: "integer"},message:"must be integer"};
if(vErrors === null){
vErrors = [err14];
}
else {
vErrors.push(err14);
}
errors++;
}
if((typeof data3 == "number") && (isFinite(data3))){
if(data3 < 1 || isNaN(data3)){
const err15 = {instancePath:instancePath+"/revision",schemaPath:"#/properties/revision/minimum",keyword:"minimum",params:{comparison: ">=", limit: 1},message:"must be >= 1"};
if(vErrors === null){
vErrors = [err15];
}
else {
vErrors.push(err15);
}
errors++;
}
}
}
if(data.revision_known_at !== undefined){
let data4 = data.revision_known_at;
if(typeof data4 === "string"){
if(!(formats0(data4))){
const err16 = {instancePath:instancePath+"/revision_known_at",schemaPath:"#/properties/revision_known_at/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err16];
}
else {
vErrors.push(err16);
}
errors++;
}
}
else {
const err17 = {instancePath:instancePath+"/revision_known_at",schemaPath:"#/properties/revision_known_at/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err17];
}
else {
vErrors.push(err17);
}
errors++;
}
}
if(data.ruleset_version !== undefined){
let data5 = data.ruleset_version;
if(typeof data5 === "string"){
if(func3(data5) > 100){
const err18 = {instancePath:instancePath+"/ruleset_version",schemaPath:"#/properties/ruleset_version/maxLength",keyword:"maxLength",params:{limit: 100},message:"must NOT have more than 100 characters"};
if(vErrors === null){
vErrors = [err18];
}
else {
vErrors.push(err18);
}
errors++;
}
if(func3(data5) < 1){
const err19 = {instancePath:instancePath+"/ruleset_version",schemaPath:"#/properties/ruleset_version/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err19];
}
else {
vErrors.push(err19);
}
errors++;
}
}
else {
const err20 = {instancePath:instancePath+"/ruleset_version",schemaPath:"#/properties/ruleset_version/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err20];
}
else {
vErrors.push(err20);
}
errors++;
}
}
if(data.supersedes_revision !== undefined){
let data6 = data.supersedes_revision;
const _errs16 = errors;
let valid2 = false;
const _errs17 = errors;
if(!(((typeof data6 == "number") && (!(data6 % 1) && !isNaN(data6))) && (isFinite(data6)))){
const err21 = {instancePath:instancePath+"/supersedes_revision",schemaPath:"#/properties/supersedes_revision/anyOf/0/type",keyword:"type",params:{type: "integer"},message:"must be integer"};
if(vErrors === null){
vErrors = [err21];
}
else {
vErrors.push(err21);
}
errors++;
}
if((typeof data6 == "number") && (isFinite(data6))){
if(data6 < 1 || isNaN(data6)){
const err22 = {instancePath:instancePath+"/supersedes_revision",schemaPath:"#/properties/supersedes_revision/anyOf/0/minimum",keyword:"minimum",params:{comparison: ">=", limit: 1},message:"must be >= 1"};
if(vErrors === null){
vErrors = [err22];
}
else {
vErrors.push(err22);
}
errors++;
}
}
var _valid0 = _errs17 === errors;
valid2 = valid2 || _valid0;
const _errs19 = errors;
if(data6 !== null){
const err23 = {instancePath:instancePath+"/supersedes_revision",schemaPath:"#/properties/supersedes_revision/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err23];
}
else {
vErrors.push(err23);
}
errors++;
}
var _valid0 = _errs19 === errors;
valid2 = valid2 || _valid0;
if(!valid2){
const err24 = {instancePath:instancePath+"/supersedes_revision",schemaPath:"#/properties/supersedes_revision/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err24];
}
else {
vErrors.push(err24);
}
errors++;
}
else {
errors = _errs16;
if(vErrors !== null){
if(_errs16){
vErrors.length = _errs16;
}
else {
vErrors = null;
}
}
}
}
}
else {
const err25 = {instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err25];
}
else {
vErrors.push(err25);
}
errors++;
}
validate52.errors = vErrors;
return errors === 0;
}
validate52.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};


function validate51(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}, isAllOfVariant = 0}={}){
let vErrors = null;
let errors = 0;
const evaluated0 = validate51.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.has_older === undefined){
const err0 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "has_older"},message:"must have required property '"+"has_older"+"'"};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
for(const key0 in data){
if(!((((key0 === "before_revision") || (key0 === "has_older")) || (key0 === "items")) || (key0 === "next_before_revision"))){
const err1 = {instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
}
if(data.before_revision !== undefined){
let data0 = data.before_revision;
const _errs4 = errors;
let valid1 = false;
const _errs5 = errors;
if(!(((typeof data0 == "number") && (!(data0 % 1) && !isNaN(data0))) && (isFinite(data0)))){
const err2 = {instancePath:instancePath+"/before_revision",schemaPath:"#/properties/before_revision/anyOf/0/type",keyword:"type",params:{type: "integer"},message:"must be integer"};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
if((typeof data0 == "number") && (isFinite(data0))){
if(data0 < 1 || isNaN(data0)){
const err3 = {instancePath:instancePath+"/before_revision",schemaPath:"#/properties/before_revision/anyOf/0/minimum",keyword:"minimum",params:{comparison: ">=", limit: 1},message:"must be >= 1"};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
}
}
var _valid0 = _errs5 === errors;
valid1 = valid1 || _valid0;
const _errs7 = errors;
if(data0 !== null){
const err4 = {instancePath:instancePath+"/before_revision",schemaPath:"#/properties/before_revision/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err4];
}
else {
vErrors.push(err4);
}
errors++;
}
var _valid0 = _errs7 === errors;
valid1 = valid1 || _valid0;
if(!valid1){
const err5 = {instancePath:instancePath+"/before_revision",schemaPath:"#/properties/before_revision/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err5];
}
else {
vErrors.push(err5);
}
errors++;
}
else {
errors = _errs4;
if(vErrors !== null){
if(_errs4){
vErrors.length = _errs4;
}
else {
vErrors = null;
}
}
}
}
if(data.has_older !== undefined){
if(typeof data.has_older !== "boolean"){
const err6 = {instancePath:instancePath+"/has_older",schemaPath:"#/properties/has_older/type",keyword:"type",params:{type: "boolean"},message:"must be boolean"};
if(vErrors === null){
vErrors = [err6];
}
else {
vErrors.push(err6);
}
errors++;
}
}
if(data.items !== undefined){
let data2 = data.items;
if(Array.isArray(data2)){
if(data2.length > 50){
const err7 = {instancePath:instancePath+"/items",schemaPath:"#/properties/items/maxItems",keyword:"maxItems",params:{limit: 50},message:"must NOT have more than 50 items"};
if(vErrors === null){
vErrors = [err7];
}
else {
vErrors.push(err7);
}
errors++;
}
const len0 = data2.length;
for(let i0=0; i0<len0; i0++){
let data3 = data2[i0];
const visitedNodes0 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate52) || new Set();
if(!visitedNodes0.has(data3)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate52, visitedNodes0);
const dataNode = data3;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes0.add(dataNode);
if(!(validate52(data3, {instancePath:instancePath+"/items/" + i0,parentData:data2,parentDataProperty:i0,rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate52.errors : vErrors.concat(validate52.errors);
errors = vErrors.length;
}
visitedNodes0.delete(dataNode);
}
}
}
else {
const err8 = {instancePath:instancePath+"/items",schemaPath:"#/properties/items/type",keyword:"type",params:{type: "array"},message:"must be array"};
if(vErrors === null){
vErrors = [err8];
}
else {
vErrors.push(err8);
}
errors++;
}
}
if(data.next_before_revision !== undefined){
let data4 = data.next_before_revision;
const _errs15 = errors;
let valid4 = false;
const _errs16 = errors;
if(!(((typeof data4 == "number") && (!(data4 % 1) && !isNaN(data4))) && (isFinite(data4)))){
const err9 = {instancePath:instancePath+"/next_before_revision",schemaPath:"#/properties/next_before_revision/anyOf/0/type",keyword:"type",params:{type: "integer"},message:"must be integer"};
if(vErrors === null){
vErrors = [err9];
}
else {
vErrors.push(err9);
}
errors++;
}
if((typeof data4 == "number") && (isFinite(data4))){
if(data4 < 1 || isNaN(data4)){
const err10 = {instancePath:instancePath+"/next_before_revision",schemaPath:"#/properties/next_before_revision/anyOf/0/minimum",keyword:"minimum",params:{comparison: ">=", limit: 1},message:"must be >= 1"};
if(vErrors === null){
vErrors = [err10];
}
else {
vErrors.push(err10);
}
errors++;
}
}
var _valid1 = _errs16 === errors;
valid4 = valid4 || _valid1;
const _errs18 = errors;
if(data4 !== null){
const err11 = {instancePath:instancePath+"/next_before_revision",schemaPath:"#/properties/next_before_revision/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err11];
}
else {
vErrors.push(err11);
}
errors++;
}
var _valid1 = _errs18 === errors;
valid4 = valid4 || _valid1;
if(!valid4){
const err12 = {instancePath:instancePath+"/next_before_revision",schemaPath:"#/properties/next_before_revision/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err12];
}
else {
vErrors.push(err12);
}
errors++;
}
else {
errors = _errs15;
if(vErrors !== null){
if(_errs15){
vErrors.length = _errs15;
}
else {
vErrors = null;
}
}
}
}
}
else {
const err13 = {instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err13];
}
else {
vErrors.push(err13);
}
errors++;
}
validate51.errors = vErrors;
return errors === 0;
}
validate51.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};


function validate40(data, {instancePath="", parentData, parentDataProperty, rootData=data, dynamicAnchors={}, isAllOfVariant = 0}={}){
/*# sourceURL="https://stock-market-agent.local/schemas/events/event-evidence-view.v1.2.schema.json" */;
let vErrors = null;
let errors = 0;
const evaluated0 = validate40.evaluated;
if(evaluated0.dynamicProps){
evaluated0.props = undefined;
}
if(evaluated0.dynamicItems){
evaluated0.items = undefined;
}
if(data && typeof data == "object" && !Array.isArray(data)){
if(data.schema_version === undefined){
const err0 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "schema_version"},message:"must have required property '"+"schema_version"+"'"};
if(vErrors === null){
vErrors = [err0];
}
else {
vErrors.push(err0);
}
errors++;
}
if(data.event_id === undefined){
const err1 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "event_id"},message:"must have required property '"+"event_id"+"'"};
if(vErrors === null){
vErrors = [err1];
}
else {
vErrors.push(err1);
}
errors++;
}
if(data.cluster_id === undefined){
const err2 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "cluster_id"},message:"must have required property '"+"cluster_id"+"'"};
if(vErrors === null){
vErrors = [err2];
}
else {
vErrors.push(err2);
}
errors++;
}
if(data.revision === undefined){
const err3 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "revision"},message:"must have required property '"+"revision"+"'"};
if(vErrors === null){
vErrors = [err3];
}
else {
vErrors.push(err3);
}
errors++;
}
if(data.change_type === undefined){
const err4 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "change_type"},message:"must have required property '"+"change_type"+"'"};
if(vErrors === null){
vErrors = [err4];
}
else {
vErrors.push(err4);
}
errors++;
}
if(data.instrument_ids === undefined){
const err5 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "instrument_ids"},message:"must have required property '"+"instrument_ids"+"'"};
if(vErrors === null){
vErrors = [err5];
}
else {
vErrors.push(err5);
}
errors++;
}
if(data.event_type === undefined){
const err6 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "event_type"},message:"must have required property '"+"event_type"+"'"};
if(vErrors === null){
vErrors = [err6];
}
else {
vErrors.push(err6);
}
errors++;
}
if(data.title === undefined){
const err7 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "title"},message:"must have required property '"+"title"+"'"};
if(vErrors === null){
vErrors = [err7];
}
else {
vErrors.push(err7);
}
errors++;
}
if(data.as_of === undefined){
const err8 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "as_of"},message:"must have required property '"+"as_of"+"'"};
if(vErrors === null){
vErrors = [err8];
}
else {
vErrors.push(err8);
}
errors++;
}
if(data.revision_known_at === undefined){
const err9 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "revision_known_at"},message:"must have required property '"+"revision_known_at"+"'"};
if(vErrors === null){
vErrors = [err9];
}
else {
vErrors.push(err9);
}
errors++;
}
if(data.extraction_version === undefined){
const err10 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "extraction_version"},message:"must have required property '"+"extraction_version"+"'"};
if(vErrors === null){
vErrors = [err10];
}
else {
vErrors.push(err10);
}
errors++;
}
if(data.ruleset_version === undefined){
const err11 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "ruleset_version"},message:"must have required property '"+"ruleset_version"+"'"};
if(vErrors === null){
vErrors = [err11];
}
else {
vErrors.push(err11);
}
errors++;
}
if(data.audit_id === undefined){
const err12 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "audit_id"},message:"must have required property '"+"audit_id"+"'"};
if(vErrors === null){
vErrors = [err12];
}
else {
vErrors.push(err12);
}
errors++;
}
if(data.revision_timeline === undefined){
const err13 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "revision_timeline"},message:"must have required property '"+"revision_timeline"+"'"};
if(vErrors === null){
vErrors = [err13];
}
else {
vErrors.push(err13);
}
errors++;
}
if(data.evidence === undefined){
const err14 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "evidence"},message:"must have required property '"+"evidence"+"'"};
if(vErrors === null){
vErrors = [err14];
}
else {
vErrors.push(err14);
}
errors++;
}
if(data.runtime_mode === undefined){
const err15 = {instancePath,schemaPath:"#/required",keyword:"required",params:{missingProperty: "runtime_mode"},message:"must have required property '"+"runtime_mode"+"'"};
if(vErrors === null){
vErrors = [err15];
}
else {
vErrors.push(err15);
}
errors++;
}
for(const key0 in data){
if(!(func1.call(schema56.properties, key0))){
const err16 = {instancePath,schemaPath:"#/additionalProperties",keyword:"additionalProperties",params:{additionalProperty: key0},message:"must NOT have additional properties"};
if(vErrors === null){
vErrors = [err16];
}
else {
vErrors.push(err16);
}
errors++;
}
}
if(data.as_of !== undefined){
let data0 = data.as_of;
if(typeof data0 === "string"){
if(!(formats0(data0))){
const err17 = {instancePath:instancePath+"/as_of",schemaPath:"#/properties/as_of/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err17];
}
else {
vErrors.push(err17);
}
errors++;
}
}
else {
const err18 = {instancePath:instancePath+"/as_of",schemaPath:"#/properties/as_of/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err18];
}
else {
vErrors.push(err18);
}
errors++;
}
}
if(data.audit_id !== undefined){
let data1 = data.audit_id;
if(typeof data1 === "string"){
if(!(formats2.test(data1))){
const err19 = {instancePath:instancePath+"/audit_id",schemaPath:"#/properties/audit_id/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err19];
}
else {
vErrors.push(err19);
}
errors++;
}
}
else {
const err20 = {instancePath:instancePath+"/audit_id",schemaPath:"#/properties/audit_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err20];
}
else {
vErrors.push(err20);
}
errors++;
}
}
if(data.change_type !== undefined){
let data2 = data.change_type;
if(typeof data2 !== "string"){
const err21 = {instancePath:instancePath+"/change_type",schemaPath:"#/$defs/BriefingChangeType/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err21];
}
else {
vErrors.push(err21);
}
errors++;
}
if(!((((data2 === "new") || (data2 === "update")) || (data2 === "correction")) || (data2 === "contradiction"))){
const err22 = {instancePath:instancePath+"/change_type",schemaPath:"#/$defs/BriefingChangeType/enum",keyword:"enum",params:{allowedValues: schema57.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err22];
}
else {
vErrors.push(err22);
}
errors++;
}
}
if(data.cluster_id !== undefined){
let data3 = data.cluster_id;
if(typeof data3 === "string"){
if(!(formats2.test(data3))){
const err23 = {instancePath:instancePath+"/cluster_id",schemaPath:"#/properties/cluster_id/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err23];
}
else {
vErrors.push(err23);
}
errors++;
}
}
else {
const err24 = {instancePath:instancePath+"/cluster_id",schemaPath:"#/properties/cluster_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err24];
}
else {
vErrors.push(err24);
}
errors++;
}
}
if(data.correction !== undefined){
let data4 = data.correction;
const _errs15 = errors;
let valid2 = false;
const _errs16 = errors;
if(data4 && typeof data4 == "object" && !Array.isArray(data4)){
const tag0 = data4.kind;
if(typeof tag0 == "string"){
if(tag0 === "replacement"){
const visitedNodes0 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate41) || new Set();
if(!visitedNodes0.has(data4)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate41, visitedNodes0);
const dataNode = data4;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes0.add(dataNode);
if(!(validate41(data4, {instancePath:instancePath+"/correction",parentData:data,parentDataProperty:"correction",rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate41.errors : vErrors.concat(validate41.errors);
errors = vErrors.length;
}
visitedNodes0.delete(dataNode);
}
var props0 = true;
}
else if(tag0 === "retraction"){
const visitedNodes1 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate43) || new Set();
if(!visitedNodes1.has(data4)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate43, visitedNodes1);
const dataNode = data4;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes1.add(dataNode);
if(!(validate43(data4, {instancePath:instancePath+"/correction",parentData:data,parentDataProperty:"correction",rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate43.errors : vErrors.concat(validate43.errors);
errors = vErrors.length;
}
visitedNodes1.delete(dataNode);
}
if(props0 !== true){
props0 = true;
}
}
else {
const err25 = {instancePath:instancePath+"/correction",schemaPath:"#/properties/correction/anyOf/0/discriminator",keyword:"discriminator",params:{error: "mapping", tag: "kind", tagValue: tag0},message:"value of tag \"kind\" must be in oneOf or anyOf"};
if(vErrors === null){
vErrors = [err25];
}
else {
vErrors.push(err25);
}
errors++;
}
}
else {
const err26 = {instancePath:instancePath+"/correction",schemaPath:"#/properties/correction/anyOf/0/discriminator",keyword:"discriminator",params:{error: "tag", tag: "kind", tagValue: tag0},message:"tag \"kind\" must be string"};
if(vErrors === null){
vErrors = [err26];
}
else {
vErrors.push(err26);
}
errors++;
}
}
var _valid0 = _errs16 === errors;
valid2 = valid2 || _valid0;
const _errs20 = errors;
if(data4 !== null){
const err27 = {instancePath:instancePath+"/correction",schemaPath:"#/properties/correction/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err27];
}
else {
vErrors.push(err27);
}
errors++;
}
var _valid0 = _errs20 === errors;
valid2 = valid2 || _valid0;
if(!valid2){
const err28 = {instancePath:instancePath+"/correction",schemaPath:"#/properties/correction/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err28];
}
else {
vErrors.push(err28);
}
errors++;
}
else {
errors = _errs15;
if(vErrors !== null){
if(_errs15){
vErrors.length = _errs15;
}
else {
vErrors = null;
}
}
}
}
if(data.event_id !== undefined){
let data5 = data.event_id;
if(typeof data5 === "string"){
if(!(formats2.test(data5))){
const err29 = {instancePath:instancePath+"/event_id",schemaPath:"#/properties/event_id/format",keyword:"format",params:{format: "uuid"},message:"must match format \""+"uuid"+"\""};
if(vErrors === null){
vErrors = [err29];
}
else {
vErrors.push(err29);
}
errors++;
}
}
else {
const err30 = {instancePath:instancePath+"/event_id",schemaPath:"#/properties/event_id/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err30];
}
else {
vErrors.push(err30);
}
errors++;
}
}
if(data.event_type !== undefined){
let data6 = data.event_type;
if(typeof data6 === "string"){
if(!pattern4.test(data6)){
const err31 = {instancePath:instancePath+"/event_type",schemaPath:"#/properties/event_type/pattern",keyword:"pattern",params:{pattern: "^[a-z][a-z0-9_.-]{1,99}$"},message:"must match pattern \""+"^[a-z][a-z0-9_.-]{1,99}$"+"\""};
if(vErrors === null){
vErrors = [err31];
}
else {
vErrors.push(err31);
}
errors++;
}
}
else {
const err32 = {instancePath:instancePath+"/event_type",schemaPath:"#/properties/event_type/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err32];
}
else {
vErrors.push(err32);
}
errors++;
}
}
if(data.evidence !== undefined){
let data7 = data.evidence;
if(Array.isArray(data7)){
if(data7.length > 50){
const err33 = {instancePath:instancePath+"/evidence",schemaPath:"#/properties/evidence/maxItems",keyword:"maxItems",params:{limit: 50},message:"must NOT have more than 50 items"};
if(vErrors === null){
vErrors = [err33];
}
else {
vErrors.push(err33);
}
errors++;
}
if(data7.length < 1){
const err34 = {instancePath:instancePath+"/evidence",schemaPath:"#/properties/evidence/minItems",keyword:"minItems",params:{limit: 1},message:"must NOT have fewer than 1 items"};
if(vErrors === null){
vErrors = [err34];
}
else {
vErrors.push(err34);
}
errors++;
}
const len0 = data7.length;
for(let i0=0; i0<len0; i0++){
let data8 = data7[i0];
const visitedNodes2 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate45) || new Set();
if(!visitedNodes2.has(data8)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate45, visitedNodes2);
const dataNode = data8;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes2.add(dataNode);
if(!(validate45(data8, {instancePath:instancePath+"/evidence/" + i0,parentData:data7,parentDataProperty:i0,rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate45.errors : vErrors.concat(validate45.errors);
errors = vErrors.length;
}
visitedNodes2.delete(dataNode);
}
}
}
else {
const err35 = {instancePath:instancePath+"/evidence",schemaPath:"#/properties/evidence/type",keyword:"type",params:{type: "array"},message:"must be array"};
if(vErrors === null){
vErrors = [err35];
}
else {
vErrors.push(err35);
}
errors++;
}
}
if(data.extraction_version !== undefined){
let data9 = data.extraction_version;
if(typeof data9 === "string"){
if(func3(data9) > 100){
const err36 = {instancePath:instancePath+"/extraction_version",schemaPath:"#/properties/extraction_version/maxLength",keyword:"maxLength",params:{limit: 100},message:"must NOT have more than 100 characters"};
if(vErrors === null){
vErrors = [err36];
}
else {
vErrors.push(err36);
}
errors++;
}
if(func3(data9) < 1){
const err37 = {instancePath:instancePath+"/extraction_version",schemaPath:"#/properties/extraction_version/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err37];
}
else {
vErrors.push(err37);
}
errors++;
}
}
else {
const err38 = {instancePath:instancePath+"/extraction_version",schemaPath:"#/properties/extraction_version/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err38];
}
else {
vErrors.push(err38);
}
errors++;
}
}
if(data.facts !== undefined){
let data10 = data.facts;
if(Array.isArray(data10)){
if(data10.length > 100){
const err39 = {instancePath:instancePath+"/facts",schemaPath:"#/properties/facts/maxItems",keyword:"maxItems",params:{limit: 100},message:"must NOT have more than 100 items"};
if(vErrors === null){
vErrors = [err39];
}
else {
vErrors.push(err39);
}
errors++;
}
const len1 = data10.length;
for(let i1=0; i1<len1; i1++){
let data11 = data10[i1];
const visitedNodes3 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate49) || new Set();
if(!visitedNodes3.has(data11)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate49, visitedNodes3);
const dataNode = data11;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes3.add(dataNode);
if(!(validate49(data11, {instancePath:instancePath+"/facts/" + i1,parentData:data10,parentDataProperty:i1,rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate49.errors : vErrors.concat(validate49.errors);
errors = vErrors.length;
}
visitedNodes3.delete(dataNode);
}
}
}
else {
const err40 = {instancePath:instancePath+"/facts",schemaPath:"#/properties/facts/type",keyword:"type",params:{type: "array"},message:"must be array"};
if(vErrors === null){
vErrors = [err40];
}
else {
vErrors.push(err40);
}
errors++;
}
}
if(data.instrument_ids !== undefined){
let data12 = data.instrument_ids;
if(Array.isArray(data12)){
if(data12.length > 100){
const err41 = {instancePath:instancePath+"/instrument_ids",schemaPath:"#/properties/instrument_ids/maxItems",keyword:"maxItems",params:{limit: 100},message:"must NOT have more than 100 items"};
if(vErrors === null){
vErrors = [err41];
}
else {
vErrors.push(err41);
}
errors++;
}
if(data12.length < 1){
const err42 = {instancePath:instancePath+"/instrument_ids",schemaPath:"#/properties/instrument_ids/minItems",keyword:"minItems",params:{limit: 1},message:"must NOT have fewer than 1 items"};
if(vErrors === null){
vErrors = [err42];
}
else {
vErrors.push(err42);
}
errors++;
}
const len2 = data12.length;
for(let i2=0; i2<len2; i2++){
if(typeof data12[i2] !== "string"){
const err43 = {instancePath:instancePath+"/instrument_ids/" + i2,schemaPath:"#/properties/instrument_ids/items/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err43];
}
else {
vErrors.push(err43);
}
errors++;
}
}
}
else {
const err44 = {instancePath:instancePath+"/instrument_ids",schemaPath:"#/properties/instrument_ids/type",keyword:"type",params:{type: "array"},message:"must be array"};
if(vErrors === null){
vErrors = [err44];
}
else {
vErrors.push(err44);
}
errors++;
}
}
if(data.revision !== undefined){
let data14 = data.revision;
if(!(((typeof data14 == "number") && (!(data14 % 1) && !isNaN(data14))) && (isFinite(data14)))){
const err45 = {instancePath:instancePath+"/revision",schemaPath:"#/properties/revision/type",keyword:"type",params:{type: "integer"},message:"must be integer"};
if(vErrors === null){
vErrors = [err45];
}
else {
vErrors.push(err45);
}
errors++;
}
if((typeof data14 == "number") && (isFinite(data14))){
if(data14 < 1 || isNaN(data14)){
const err46 = {instancePath:instancePath+"/revision",schemaPath:"#/properties/revision/minimum",keyword:"minimum",params:{comparison: ">=", limit: 1},message:"must be >= 1"};
if(vErrors === null){
vErrors = [err46];
}
else {
vErrors.push(err46);
}
errors++;
}
}
}
if(data.revision_known_at !== undefined){
let data15 = data.revision_known_at;
if(typeof data15 === "string"){
if(!(formats0(data15))){
const err47 = {instancePath:instancePath+"/revision_known_at",schemaPath:"#/properties/revision_known_at/format",keyword:"format",params:{format: "date-time"},message:"must match format \""+"date-time"+"\""};
if(vErrors === null){
vErrors = [err47];
}
else {
vErrors.push(err47);
}
errors++;
}
}
else {
const err48 = {instancePath:instancePath+"/revision_known_at",schemaPath:"#/properties/revision_known_at/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err48];
}
else {
vErrors.push(err48);
}
errors++;
}
}
if(data.revision_timeline !== undefined){
let data16 = data.revision_timeline;
const visitedNodes4 = (typeof visitedNodesForRef !== 'undefined') && visitedNodesForRef.get(validate51) || new Set();
if(!visitedNodes4.has(data16)){
if (typeof visitedNodesForRef !== 'undefined') visitedNodesForRef.set(validate51, visitedNodes4);
const dataNode = data16;
if (typeof dataNode === "object" && dataNode !== null) visitedNodes4.add(dataNode);
if(!(validate51(data16, {instancePath:instancePath+"/revision_timeline",parentData:data,parentDataProperty:"revision_timeline",rootData,isAllOfVariant:0,dynamicAnchors}))){
vErrors = vErrors === null ? validate51.errors : vErrors.concat(validate51.errors);
errors = vErrors.length;
}
visitedNodes4.delete(dataNode);
}
}
if(data.ruleset_version !== undefined){
let data17 = data.ruleset_version;
if(typeof data17 === "string"){
if(func3(data17) > 100){
const err49 = {instancePath:instancePath+"/ruleset_version",schemaPath:"#/properties/ruleset_version/maxLength",keyword:"maxLength",params:{limit: 100},message:"must NOT have more than 100 characters"};
if(vErrors === null){
vErrors = [err49];
}
else {
vErrors.push(err49);
}
errors++;
}
if(func3(data17) < 1){
const err50 = {instancePath:instancePath+"/ruleset_version",schemaPath:"#/properties/ruleset_version/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err50];
}
else {
vErrors.push(err50);
}
errors++;
}
}
else {
const err51 = {instancePath:instancePath+"/ruleset_version",schemaPath:"#/properties/ruleset_version/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err51];
}
else {
vErrors.push(err51);
}
errors++;
}
}
if(data.runtime_mode !== undefined){
let data18 = data.runtime_mode;
if(typeof data18 !== "string"){
const err52 = {instancePath:instancePath+"/runtime_mode",schemaPath:"#/$defs/SourceRuntimeModeV1_2/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err52];
}
else {
vErrors.push(err52);
}
errors++;
}
if(!((((data18 === "demo_replay") || (data18 === "dev_live")) || (data18 === "public_static")) || (data18 === "authorized"))){
const err53 = {instancePath:instancePath+"/runtime_mode",schemaPath:"#/$defs/SourceRuntimeModeV1_2/enum",keyword:"enum",params:{allowedValues: schema75.enum},message:"must be equal to one of the allowed values"};
if(vErrors === null){
vErrors = [err53];
}
else {
vErrors.push(err53);
}
errors++;
}
}
if(data.schema_version !== undefined){
let data19 = data.schema_version;
if(typeof data19 !== "string"){
const err54 = {instancePath:instancePath+"/schema_version",schemaPath:"#/properties/schema_version/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err54];
}
else {
vErrors.push(err54);
}
errors++;
}
if("1.2" !== data19){
const err55 = {instancePath:instancePath+"/schema_version",schemaPath:"#/properties/schema_version/const",keyword:"const",params:{allowedValue: "1.2"},message:"must be equal to constant"};
if(vErrors === null){
vErrors = [err55];
}
else {
vErrors.push(err55);
}
errors++;
}
}
if(data.supersedes_revision !== undefined){
let data20 = data.supersedes_revision;
const _errs51 = errors;
let valid13 = false;
const _errs52 = errors;
if(!(((typeof data20 == "number") && (!(data20 % 1) && !isNaN(data20))) && (isFinite(data20)))){
const err56 = {instancePath:instancePath+"/supersedes_revision",schemaPath:"#/properties/supersedes_revision/anyOf/0/type",keyword:"type",params:{type: "integer"},message:"must be integer"};
if(vErrors === null){
vErrors = [err56];
}
else {
vErrors.push(err56);
}
errors++;
}
if((typeof data20 == "number") && (isFinite(data20))){
if(data20 < 1 || isNaN(data20)){
const err57 = {instancePath:instancePath+"/supersedes_revision",schemaPath:"#/properties/supersedes_revision/anyOf/0/minimum",keyword:"minimum",params:{comparison: ">=", limit: 1},message:"must be >= 1"};
if(vErrors === null){
vErrors = [err57];
}
else {
vErrors.push(err57);
}
errors++;
}
}
var _valid1 = _errs52 === errors;
valid13 = valid13 || _valid1;
const _errs54 = errors;
if(data20 !== null){
const err58 = {instancePath:instancePath+"/supersedes_revision",schemaPath:"#/properties/supersedes_revision/anyOf/1/type",keyword:"type",params:{type: "null"},message:"must be null"};
if(vErrors === null){
vErrors = [err58];
}
else {
vErrors.push(err58);
}
errors++;
}
var _valid1 = _errs54 === errors;
valid13 = valid13 || _valid1;
if(!valid13){
const err59 = {instancePath:instancePath+"/supersedes_revision",schemaPath:"#/properties/supersedes_revision/anyOf",keyword:"anyOf",params:{},message:"must match a schema in anyOf"};
if(vErrors === null){
vErrors = [err59];
}
else {
vErrors.push(err59);
}
errors++;
}
else {
errors = _errs51;
if(vErrors !== null){
if(_errs51){
vErrors.length = _errs51;
}
else {
vErrors = null;
}
}
}
}
if(data.title !== undefined){
let data21 = data.title;
if(typeof data21 === "string"){
if(func3(data21) > 500){
const err60 = {instancePath:instancePath+"/title",schemaPath:"#/properties/title/maxLength",keyword:"maxLength",params:{limit: 500},message:"must NOT have more than 500 characters"};
if(vErrors === null){
vErrors = [err60];
}
else {
vErrors.push(err60);
}
errors++;
}
if(func3(data21) < 1){
const err61 = {instancePath:instancePath+"/title",schemaPath:"#/properties/title/minLength",keyword:"minLength",params:{limit: 1},message:"must NOT have fewer than 1 characters"};
if(vErrors === null){
vErrors = [err61];
}
else {
vErrors.push(err61);
}
errors++;
}
}
else {
const err62 = {instancePath:instancePath+"/title",schemaPath:"#/properties/title/type",keyword:"type",params:{type: "string"},message:"must be string"};
if(vErrors === null){
vErrors = [err62];
}
else {
vErrors.push(err62);
}
errors++;
}
}
}
else {
const err63 = {instancePath,schemaPath:"#/type",keyword:"type",params:{type: "object"},message:"must be object"};
if(vErrors === null){
vErrors = [err63];
}
else {
vErrors.push(err63);
}
errors++;
}
validate40.errors = vErrors;
return errors === 0;
}
validate40.evaluated = {"props":true,"dynamicProps":false,"dynamicItems":false};

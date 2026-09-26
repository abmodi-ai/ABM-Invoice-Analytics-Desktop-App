# ADR 0004: Party linkage uses fixed Fellegi-Sunter weights

Status: accepted

Parties number in the hundreds or low thousands per install, too few for stable EM estimation of
m/u parameters. Party matching therefore uses Fellegi-Sunter log-likelihood weights with fixed
m/u values (engine/verismo_engine/linkage/parties.py) over name (Jaro-Winkler / token-set),
remittance address and phone, blocked on name prefix, address and phone. Deterministic merges on
an exact tax-ID HMAC or NPI are applied automatically and audited. Everything else is only
*suggested* to an admin, as the spec requires. Patients, which number in the tens of thousands,
use EM-estimated parameters (ADR 0005).

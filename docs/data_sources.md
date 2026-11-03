# Data sources

## SGCC electricity theft dataset (primary dataset)

- Origin: State Grid Corporation of China (SGCC), consumer daily consumption.
- Official host: IEEE-PES Data Sharing platform. The page returned HTTP 500
  when checked on 2026-11-03, so it could not be used.
- Copy in use: Kaggle mirror "SGCC Electricity Theft Detection"
  (bensalem14/sgcc-dataset), downloaded 2026-11-03.
- Mirror license (declared by the uploader): "Database: Open Database,
  Contents: Database Contents". The original publisher's terms have NOT been
  verified. The mirror has no DOI and no listed citations.
- Redistribution: the data is never committed to this repository.

### Verified from the downloaded files

- Archive sgcc_kaggle_bensalem14.zip (52 MB) contains "data set.csv"
  (163,119,034 bytes) and "datasetsmall.csv" (3,797,746 bytes).
- "data set.csv" has 42,373 lines: 1 header + 42,372 data lines (assuming no
  line breaks inside fields; to be confirmed with a CSV parser).
- The header has 1,036 comma-separated fields. The mirror page says 1,037.
- Header order: daily date columns first (format M/D/YYYY, not zero-padded),
  then CONS_NO, then FLAG. The mirror page says CONS_NO is the first column,
  which is wrong for this file.
- First date column: 1/1/2014. Last date column: 10/31/2016. The mirror page
  says the end date is 10/30/2016.

### Open questions

- 1,036 fields minus CONS_NO and FLAG leaves 1,034 date columns, but
  2014-01-01 to 2016-10-31 spans 1,035 days. A date may be missing or the
  header may differ from expectations.
- FLAG values, class balance, duplicate or empty customer ids, and how missing
  readings are encoded are not yet checked.

### Limitations

- Chinese data. It is a public research benchmark, not Pakistani data.
- Labels come from the source and are not verified by this project.
- This project makes no claim of detecting real electricity theft in Pakistan.

### Verification status

- [x] Archive checksum recorded
- [x] Line count and header field count checked
- [x] First and last date headers read
- [ ] Date columns fully validated (gaps, duplicates, order)
- [ ] Label values and class balance confirmed
- [ ] Customer id uniqueness confirmed
- [ ] Missing-value encoding and rate measured
- [ ] Compared against the official IEEE-PES files when available

### Checksums (SHA-256)

- sgcc_kaggle_bensalem14.zip: b802c25e4fbc1977e50f6745151d406bcb9d4f39291f0414b61da1dff88f6135
- data set.csv: 99ba49d416238b400771b97aea71975706ce94989dd5440202402441cbcc4579

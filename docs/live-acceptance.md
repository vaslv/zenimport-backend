# Live acceptance and cutover

## M3 — before production cutover

1. Open VietinBank history. Without sending, check the selected Current account, row recognition, filters/pagination, and account switching. The account list remains partial until completeness is proven; unsupported currency is not imported.
2. Agree on a separate ZenMoney test VND account and one synthetic transaction. Use an isolated database/extension profile. Do not send real bank history into the test account.
3. After agreement, create one record with a frozen UUID, viewed=false, and NULL category. In the mobile app, check New, change its category/type, and synchronize again; then delete this test record in the mobile app and verify again: edits are not reverted and the record is not restored.
4. Record actual results and limitations; obtain acceptance before M4. Fixtures do not replace these checks.

## M4 — separate production cutover

1. Stop the old writer/extension and ensure there is no second sender. Take a fresh consistent production schema 12 backup instead of reusing the old rehearsal snapshot.
2. Convert into a new explicit volume and repeat M1/M2 checks. Keep the original for recovery. Configure the backend API token and SDK token only on the server; give the extension only the backend token.
3. Start the new backend and configure account mappings/periods. First read current ZenMoney and verify unfinished UUIDs. New delivery requires the Synchronize command.
4. Update/connect Chrome to the new backend and check one complete period and mobile results. The old server is incompatible with the new extension.
5. When rolling back after any new writes, preserve the fresh v5 database and UUID/attempt journal. Returning only to legacy schema 12 can lose delivery information and create duplicates, so do not simply restart the old writer.

Until M3/M4 are complete, production has not been switched. Automated tests send nothing to real ZenMoney.

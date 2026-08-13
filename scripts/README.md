# Scripts

Only verified P0 operational scripts are kept here. Each script is added by the Gate that first requires its behavior.

`reset-demo.ps1` requires the WPF demo to be stopped. It clears the demo application's local SQLite outbox by default. Pass `-ResetCloud -ProjectId <id> -ConfirmProjectId <id>` only when the matching project's demo incident records must also be removed.

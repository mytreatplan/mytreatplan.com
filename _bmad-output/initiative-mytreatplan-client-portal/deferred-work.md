- source_plan: `epic-landing-integration/story-staging-environment-and-deploys-plan.md`
  summary: Staging provisioning, Nginx/systemd config, runbook and the GitHub Actions staging deploy, split off as ticket 1.13.
  evidence: The story 1.8 plan was ~2,400 tokens; the user chose to split it at the size gate (2026-10-11).
- source_plan: `_bmad-output/initiative-mytreatplan-client-portal/epic-landing-integration/story-off-region-database-backups-and-restore-test-plan.md`
  summary: Protect off-region dumps from being overwritten by the server's key (bucket versioning with noncurrent-version expiry, or object lock), decided for production in 1.12.
  evidence: The server's S3 user has `s3:PutObject` without delete; with no versioning or object lock it can still overwrite an existing dump (review 1.14, finding 2).

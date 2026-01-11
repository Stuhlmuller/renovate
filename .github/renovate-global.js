module.exports = {
	platform: "github",
	globalExtends: [
		"config:best-practices",
		":semanticCommits",
		":automergeMinor",
		":automergeBranch",
		"schedule:automergeWeekly",
		":rebaseStalePrs"
	],
	repositories: [],
	platformCommit: "enabled",
	autodiscover: true,
	packageRules: [
		{
			matchUpdateTypes: ["minor", "patch"],
			matchCurrentVersion: "!/^0/",
			automerge: true
		}
	]
};

module.exports = {
	platform: "github",
	globalExtends: [
		"config:best-practices",
		":automergeBranch",
		"schedule:automergeDaily"
	],
	platformCommit: "enabled",
	autodiscover: true,
	onboarding: false,
	requireConfig: "optional",
	labels: ["dependencies"],
	vulnerabilityAlerts: {
		enabled: true,
		labels: [
			"security"
		]
  }
};

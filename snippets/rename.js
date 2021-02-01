function rename(node) {
	const info = JSON.parse(node.ProxyInfo);
	const hostname = info.Hostname;
	if (hostname.includes("clashcloud"))
		return node.Remark + " |薯条";
	return node.Remark;
}

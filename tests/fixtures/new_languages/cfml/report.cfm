<cfinclude template="partials/footer.cfm">

<cffunction name="formatRow" returntype="string">
	<cfargument name="row">
	<cfreturn row>
</cffunction>

<cfscript>
	pricing = createObject("component", "helpers.Pricing");
	rows = formatRow(1);
</cfscript>

<cfset order = new Order()>

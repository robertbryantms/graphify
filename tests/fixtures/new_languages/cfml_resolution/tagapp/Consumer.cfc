<cfcomponent output="false">
	<cffunction name="go" access="public" returntype="any">
		<cfset var fmt = createObject("component", "ext.Formatter")>
		<cfreturn fmt>
	</cffunction>
</cfcomponent>

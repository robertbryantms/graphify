<cfcomponent displayname="Pricing" output="false">

	<cffunction name="quote" access="public" returntype="numeric">
		<cfargument name="units" type="numeric">
		<cfreturn arguments.units * 2>
	</cffunction>

</cfcomponent>

<cfcomponent displayname="Order" extends="Base" implements="IAuditable" output="false">

	<cfproperty name="prices" inject="priceService@catalog">

	<cffunction name="total" access="public" returntype="numeric">
		<cfset var pricing = createObject("component", "helpers.Pricing")>
		<cfset var clock = createObject("java", "java.util.Date")>
		<cfinvoke component="Base" method="describe" returnvariable="label">
		<cfset var amount = applyDiscount(5)>
		<cfreturn amount>
	</cffunction>

	<cffunction name="applyDiscount" access="private" returntype="numeric">
		<cfargument name="percent" type="numeric">
		<cfreturn arguments.percent>
	</cffunction>

	<cffunction name="render" access="public" returntype="string">
		<cfinclude template="partials/footer.cfm">
		<cfinclude template="/shared/menu.cfm">
		<cfobject component="Mailer" name="mailer">
		<cfreturn "">
	</cffunction>

</cfcomponent>

#!/usr/bin/env python3

# Import the Halite SDK, which will let you interact with the game.
import hlt
from hlt import constants

import random
import logging
import copy
from enum import IntEnum, auto

# v1  base bot 
# v2  better movement
# v3  goal based movement
# v4  stuck resolution
# v5  Fib based ship cost, no limit, no delay, return when closer to full
# v6  Homing at end, start with RETURNING, EXPLORE range increase over game after RETURNING, 
#     +1 range on EXPLORING, fix no move bug, strip mine
# v7  Third Fib for ship cost hike, stop shipbuilding when HOMING, HOMING sooner because more ships,
#     Dropoffs, progressively deeper extraction
# v8  Fix log crash, smaller create delay, lower dropoffoverhead
# v9  Dropoff pos range smaller, Pause every other frame when RETURNING, Fix return to storage bug, limit ship creation to first 80%
# v10 Two-third fib, extract more, 
# v11 must move if no halite
# v12 radial explore, don't pause if too little halite, fix pause/unpause, fibbing ratio by map size
# v13 dropoff fixes - check when converting, dropoff count based on map size, dropoff count also based on average distance(?), planned_dropoffs
# v14 here and near ship switch for dropoff, 10% dropoff overhead, one less dropoff for 32x32, closer average distance for conversion
# v15 Metrics (shipfib, max_dropoff) based on map size + player count, return with smaller loads as game progresses, kamikaze enemy ships on dropoffs, 
#     fix for ships stalled on dropoff
# v16 Better nav to avoid self collisions, better radial org, don't kamikaze self on dropoffs

class shipInfo(IntEnum):
    STATE = 0
    GOAL = auto()
    LASTPOS = auto()
    PAUSE = auto()
    DROPID = auto()
    TURNTAKEN = auto()
#

class shipState(IntEnum):
    RETURNING = auto()
    EXPLORING = auto()
    CONVERTING = auto()
    HOMING = auto()
#

class dropInfo(IntEnum):
    RADIAL = 0
    SHIP_HERE = auto()
    SHIP_NEAR = auto()
#

verbose = False
useSaboteurs = False
homing_begun = False
reservedfordropoff = 0
dropoffcostoverhead = 1.1
createshipturn = 0
radial = [[0, 1],[1, 0],[0, -1],[-1, 0],[1, 1],[1, -1],[-1, -1],[-1, 1]]
ship_status = {}
dropoff_status = {}
planned_dropoffs = {}
nav_plan = {}
sizeratio2 = {
    32:{2:[0.67,2], 4:[0.75,1]}, 
    40:{2:[0.60,3], 4:[0.67,1]}, 
    48:{2:[0.40,4], 4:[0.50,2]}, 
    56:{2:[0.33,4], 4:[0.45,2]}, 
    64:{2:[0.25,5], 4:[0.40,3]}
}
max_dropoffs = 1



def fibbing(n):
    if n == 0:
        return 0
    elif n==1 or n==2:
        return 1
        
    return fibbing(n-1)+fibbing(n-2)
#

def GetFib(fn, arg):
    fibs = {}
    if arg not in fibs:
        fibs[arg] = fn(arg)
    return fibs[arg]
#

def GetShipBuildThreshold(num):
    return int(constants.SHIP_COST * (1+(GetFib(fibbing, num)/100))) + reservedfordropoff
#    

def IsAtEdgeOfMap( position, map ):
    if (position.x == 0 or position.x == map.width-1 or position.y == 0 or position.y == map.height-1 ):
        return True
    #
    return False
#

def PositionToNavIndex( position, the_map ):
    norm = the_map.normalize(position)
    return (norm.x * the_map.width + norm.y)
#

def GetRichestPosition( curPos, range, mustmove, avoidedges, map ):
    global nav_plan
    
    first = mustmove
    range *= 4
    best = curPos
    max = map[curPos].halite_amount
    adList = curPos.get_surrounding_cardinals()
    for adjacent in adList:
        if range > 0:
            for ad1 in adjacent.get_surrounding_cardinals():
                adList.append(map.normalize(ad1))
            #
            range -= 1
        #
        nav_idx = PositionToNavIndex( adjacent, map )
        if map[adjacent].is_empty and not nav_idx in nav_plan and \
            ((first and map[adjacent].halite_amount >= max) or (map[adjacent].halite_amount > max)):
            
            adjacent = map.normalize(adjacent)
            if avoidedges:
                if not IsAtEdgeOfMap(ad1, map):
                    best = adjacent
                    max = map[adjacent].halite_amount
                    first = False
                #
            else:
                best = adjacent
                max = map[adjacent].halite_amount
                first = False
            #
        #
    #
    return best
#

def ConvertToDropoff(ship, me, av_storage_dist, map):
    global planned_dropoffs
    global max_dropoffs
    
    if ((av_storage_dist > map.height / 3) or len(me.get_ships()) > (1+len(me.get_dropoffs()))*10) and \
        (me.halite_amount > int(constants.DROPOFF_COST * dropoffcostoverhead)) and \
        (len(me.get_dropoffs()) + len(planned_dropoffs) < max_dropoffs):
        
        far_enough = map.calculate_distance(ship.position, me.shipyard.position) > map.height / 3
        for dropoff in me.get_dropoffs():
            far_enough = far_enough and (map.calculate_distance(ship.position, dropoff.position) > map.height / 3)
        #
        for dropoff, position in planned_dropoffs.items():
            #logging.info("Planned {}".format(dropoff))
            far_enough = far_enough and (map.calculate_distance(ship.position, position) > map.height / 3)
        #
        if far_enough:
            dropoffpos = GetRichestPosition( ship.position, 2, False, True, map )
            planned_dropoffs[ship.id] = dropoffpos
            return True, dropoffpos
        #
    #
    return False, None    
#

def GetClosestStoragePosition(position, me, map):
    storageindex = 0
    min = map.calculate_distance(ship.position, me.shipyard.position)
    pos = me.shipyard.position
    for dropoff in me.get_dropoffs():
        dist = map.calculate_distance(ship.position, dropoff.position)
        if (dist <= min):
            min = dist
            pos = dropoff.position
            storageindex = dropoff.id
        #
    #
    return pos,storageindex
#

def GetRadialExplorePos( pos, dropid ):
    global radial
    global dropoff_status
    if dropid not in dropoff_status:
        dropoff_status[dropid] = [0, None, None]
    #
    mul = 1+int(dropoff_status[dropid][dropInfo.RADIAL]/8)
    idx = dropoff_status[dropid][dropInfo.RADIAL] % 8
    offset = radial[idx]
    radpos = copy.deepcopy(pos)
    radpos.x += offset[0]*mul
    radpos.y += offset[1]*mul
    dropoff_status[dropid][dropInfo.RADIAL] += 1
    return radpos
#

def UpdateNavPlan(the_ship, the_map, position):
    global ship_status
    global nav_plan

	# mark the nav_map
    nav_idx = PositionToNavIndex(position, the_map)
    nav_plan[nav_idx] = True
	
	# mark the map
    the_map[position].mark_unsafe(the_ship)
	
	# mark the ship status
    ship_status[the_ship.id][shipInfo.PAUSE] = (the_ship.position == position)
    ship_status[the_ship.id][shipInfo.TURNTAKEN] = True
#

def PauseShip(the_ship, the_map, command_buffer):
    global ship_status
    global nav_plan
    global verbose
	
    if verbose:
        logging.info("Ship {} Pausing".format(the_ship.id))
    #
    
    # Update Nav Details
    UpdateNavPlan(the_ship, the_map, the_ship.position)
	
	# add the command
    command_buffer.append(the_ship.stay_still())
#

def SideStepShip(the_ship, the_map, command_buffer):
    global ship_status
    global nav_plan
    global verbose
	
    if verbose:
        logging.info("Ship {} trying SideStep".format(the_ship.id))
    #
    adjacent = GetRichestPosition(the_ship.position, 0, True, False, the_map)
    if adjacent != the_ship.position:
        UpdateNavPlan(the_ship, the_map, adjacent)
    	
        # add the command
        moves = the_map.get_unsafe_moves(the_ship.position, adjacent)
        logging.info("Current {} Goal {} Same {} Moves {}".format(the_ship.position, adjacent, (the_ship.position==adjacent), moves))
        command_buffer.append(the_ship.move(moves[0]))

        if verbose:
            logging.info("Ship {} Goal {} Move {}".format(the_ship.id, adjacent, move))
        #
        return True
    #
    return False
#


def NavigateShip(the_ship, the_map, command_buffer):
    global ship_status
    global nav_plan
    global verbose
	
    if verbose:
        logging.info("Ship {} Navigating".format(the_ship.id))
	#
	
    move = the_map.naive_navigate(the_ship, ship_status[the_ship.id][shipInfo.GOAL])
    if verbose:
        logging.info("Ship {} Current {} Goal {} Move {}".format(the_ship.id, the_ship.position, ship_status[the_ship.id][shipInfo.GOAL], move))
    #
    
    # compute new postion
    newpos = the_ship.position.directional_offset(move)
    
    # compute the nav_map index
    nav_idx = PositionToNavIndex(newpos, the_map)
    if newpos == the_ship.position or nav_idx in nav_plan:
        # check if ship can side step
        if not SideStepShip(the_ship, the_map, command_buffer):
            # Pause if nowhere to go
            PauseShip(the_ship, the_map, command_buffer)
            return False, 0
        #
    else:   
        UpdateNavPlan(the_ship, the_map, newpos)
    	
        # add the command
        command_buffer.append(the_ship.move(move))
        
        if verbose:
            logging.info("Ship {} Move {}".format(the_ship.id, move))
        #
    #
    
    # return cost to move
    return True, int(the_map[the_ship.position].halite_amount * 0.1)
#


# This game object contains the initial game state 
game = hlt.Game()
# Respond with your name.
game.ready("DeepCv16")

shipfibratio = sizeratio2[game.game_map.height][len(game.players)][0]
max_dropoffs = sizeratio2[game.game_map.height][len(game.players)][1]

while True:
    # Get the latest game state.
    game.update_frame()
    # You extract player metadata and the updated map metadata here for convenience.
    me = game.me
    game_map = game.game_map
    
    # clear the nav_plan for the turn
    nav_plan.clear()

    # setup parameters for this turn    
    extractionratio = 25 + ( int(game.turn_number/100) * 5 )
    min_halite = constants.MAX_HALITE / extractionratio
    return_threshold = int(constants.MAX_HALITE * 0.5) #int(constants.MAX_HALITE * (0.5-(0.25*game.turn_number/constants.MAX_TURNS)))
    
    # A command queue holds all the commands you will run this turn.
    command_queue = []
    
    numships = len(me.get_ships())
    numdropoffs = len(me.get_dropoffs())
    exploring = 0
    returning = 0
    av_storage_dist = 0
    if verbose:
        logging.info("PRE-PASS")
    #
    for ship in me.get_ships():
        if ship.id not in ship_status:
            ship_status[ship.id] = [shipState.RETURNING, ship.position, ship.position, False, 0, False]
            if verbose:
                logging.info("New Ship {}".format(ship.id))
            #
        elif ship_status[ship.id][shipInfo.STATE] == shipState.RETURNING:
            if verbose:
                logging.info("Ship {} RETURNING".format(ship.id))
            #
            if True:
                if ship.position == ship_status[ship.id][shipInfo.LASTPOS] and not ship_status[ship.id][shipInfo.PAUSE]: 
                    ship_status[ship.id][shipInfo.GOAL] = GetRichestPosition(ship.position, 0, True, False, game_map)
                    if verbose:
                        logging.info("Ship {} didn't move last frame".format(ship.id))
                    #
                #
                ship_status[ship.id][shipInfo.PAUSE] = not ship_status[ship.id][shipInfo.PAUSE]
                returning += 1
            else:            
                if ship.position == ship_status[ship.id][shipInfo.LASTPOS] and not ship_status[ship.id][shipInfo.PAUSE]: 
                    ship_status[ship.id][shipInfo.STATE] = shipState.EXPLORING
                    ship_status[ship.id][shipInfo.GOAL] = None
                    exploring += 1
                    closest, index = GetClosestStoragePosition(ship.position, me, game_map)
                    av_storage_dist += game_map.calculate_distance(ship.position, closest)
                else:
                    ship_status[ship.id][shipInfo.PAUSE] = not ship_status[ship.id][shipInfo.PAUSE]
                    returning += 1
                #
            #
        elif ship_status[ship.id][shipInfo.STATE] == shipState.EXPLORING:
            if verbose:
                logging.info("Ship {} EXPLORING".format(ship.id))
            #
            exploring += 1
            if ship.position == ship_status[ship.id][shipInfo.LASTPOS] and not ship_status[ship.id][shipInfo.PAUSE]:
                #ship_status[ship.id][shipInfo.GOAL] = GetRichestPosition(ship.position, 0, True, False, game_map)
                if verbose:
                    logging.info("Ship {} didn't move last frame".format(ship.id))
                #
            #
            closest, index = GetClosestStoragePosition(ship.position, me, game_map)
            av_storage_dist += game_map.calculate_distance(ship.position, closest)
            ship_status[ship.id][shipInfo.PAUSE] = False
        #
        if ship_status[ship.id][shipInfo.STATE] != shipState.HOMING:
            turns_left = constants.MAX_TURNS - game.turn_number
            if turns_left < 100:
                storage, id = GetClosestStoragePosition(ship.position, me, game_map)
                distance = game_map.calculate_distance(ship.position, storage)
                if (distance * 2.5) > turns_left:
                    if ship_status[ship.id][shipInfo.STATE] == shipState.EXPLORING:
                        exploring -= 1
                    elif ship_status[ship.id][shipInfo.STATE] == shipState.RETURNING:
                        returning -= 1
                    elif ship_status[ship.id][shipInfo.STATE] == shipState.CONVERTING:
                        reservedfordropoff -= int(constants.DROPOFF_COST * dropoffcostoverhead)
                    #
                    ship_status[ship.id][shipInfo.STATE] = shipState.HOMING
                    ship_status[ship.id][shipInfo.GOAL] = storage
                    homing_begun = True
                    #logging.info("Ship {} HOMING Distance {}".format(ship.id, distance))
                #
            #
        #
        #logging.info("Ship {} state {} goal {} pos {} halite {}.".format(ship.id, str(ship_status[ship.id][shipInfo.STATE]), ship_status[ship.id][shipInfo.GOAL], ship.position, ship.halite_amount))
        ship_status[ship.id][shipInfo.LASTPOS] = ship.position
        ship_status[ship.id][shipInfo.TURNTAKEN] = False
    #
    #logging.info("PrePro Complete")
    if exploring > 0:
        av_storage_dist /= exploring
    #
    dropoffthisturn = False
    costthisturn = 0
    
    ship_near_shipyard = False

    if verbose:
        logging.info("MAIN-PASS")
    #
    for ship in me.get_ships():        
        if ship_status[ship.id][shipInfo.STATE] == shipState.RETURNING:
            if verbose:
                logging.info("Ship {} RETURNING".format(ship.id))
            #
            if ship.position == ship_status[ship.id][shipInfo.GOAL]:
                ship_status[ship.id][shipInfo.STATE] = shipState.EXPLORING
                #ship_status[ship.id][shipInfo.GOAL] = GetRichestPosition( ship.position, 1, True, False, game_map )
                ship_status[ship.id][shipInfo.GOAL] = game_map.normalize(GetRadialExplorePos(ship.position, ship_status[ship.id][shipInfo.DROPID]))
                if ship_status[ship.id][shipInfo.DROPID] not in dropoff_status:
                    dropoff_status[ship_status[ship.id][shipInfo.DROPID]] = [0, None, None]
                #
                dropoff_status[ship_status[ship.id][shipInfo.DROPID]][dropInfo.SHIP_HERE] = ship.id
                if verbose:
                    logging.info("Ship {} HERE at Dropoff {}".format(ship.id, ship_status[ship.id][shipInfo.DROPID]))
                #
            elif game_map.calculate_distance(ship.position, ship_status[ship.id][shipInfo.GOAL]) == 1:
                if ship_status[ship.id][shipInfo.DROPID] not in dropoff_status:
                    dropoff_status[ship_status[ship.id][shipInfo.DROPID]] = [0, None, None]
                #
                if dropoff_status[ship_status[ship.id][shipInfo.DROPID]][dropInfo.SHIP_NEAR] == None:
                    dropoff_status[ship_status[ship.id][shipInfo.DROPID]][dropInfo.SHIP_NEAR] = ship.id
                    if ship_status[ship.id][shipInfo.DROPID] == 0:
                        ship_near_shipyard = True
                    #
                    if verbose:
                        logging.info("Ship {} first to arrive NEAR Dropoff {} ".format(ship.id, ship_status[ship.id][shipInfo.DROPID]))
                    #
                else:
                    PauseShip(ship, game_map, command_queue)
                    if verbose:
                        logging.info("Ship {} NEAR Dropoff {}. PAUSED".format(ship.id, ship_status[ship.id][shipInfo.DROPID]))
                    #
                #            
            else:
                if game_map[ship.position].halite_amount < int(min_halite/2) or not ship_status[ship.id][shipInfo.PAUSE]:
                    #success, cost = NavigateShip(ship, game_map, command_queue)
                    #costthisturn += cost
                    if verbose:
                        logging.info("Ship {} moving".format(ship.id))
                    #
                else:
                    if verbose:
                        logging.info("Ship {} pausing".format(ship.id))
                    #
                    PauseShip(ship, game_map, command_queue)
                #
            #
        elif ship_status[ship.id][shipInfo.STATE] == shipState.HOMING:
            if verbose:
                logging.info("Ship {} HOMING - crash in".format(ship.id))
            #
            if game_map.calculate_distance(ship.position, ship_status[ship.id][shipInfo.GOAL]) == 1:
                if verbose:
                    logging.info("Ship {} slam home".format(ship.id))
                #
                moves = game_map.get_unsafe_moves(ship.position, ship_status[ship.id][shipInfo.GOAL])
                command_queue.append(ship.move(moves[0]))
                ship_status[ship.id][shipInfo.TURNTAKEN] = True
            #else:
            #    success, cost = NavigateShip(ship, game_map, command_queue)
            #    costthisturn += cost
            #
        elif ship_status[ship.id][shipInfo.STATE] == shipState.CONVERTING:
            if verbose:
                logging.info("Ship {} CONVERTING".format(ship.id))
            #
            if ship.position == ship_status[ship.id][shipInfo.GOAL]:
                if me.halite_amount - costthisturn > int(constants.DROPOFF_COST * dropoffcostoverhead):
                    command_queue.append(ship.make_dropoff())
                    del planned_dropoffs[ship.id]
                    reservedfordropoff -= int(constants.DROPOFF_COST * dropoffcostoverhead)
                    costthisturn += constants.DROPOFF_COST
                    ship_status[ship.id][shipInfo.TURNTAKEN] = True
                    if verbose:
                        logging.info("Ship {} CONVERTING - convert".format(ship.id))
                    #
                else:
                    if verbose:
                        logging.info("Ship {} CONVERTING - wait".format(ship.id))
                    #
                    PauseShip(ship, game_map, command_queue)
                #
            #else:
            #    if verbose:
            #        logging.info("Ship {} CONVERTING - moving".format(ship.id))
            #    #
            #    success, cost = NavigateShip(ship, game_map, command_queue)
            #    costthisturn += cost
            #
        elif ship_status[ship.id][shipInfo.STATE] == shipState.EXPLORING:
            if verbose:
                logging.info("Ship {} EXPLORING".format(ship.id))
            #
            if ship.is_full:
                if verbose:
                    logging.info("Ship {} EXPLORING - full".format(ship.id))
                #
                convert = False
                dropoffpos = ship.position
                if not dropoffthisturn:
                    convert, dropoffpos = ConvertToDropoff(ship, me, av_storage_dist, game_map)
                #
                if convert:
                    ship_status[ship.id][shipInfo.STATE] = shipState.CONVERTING
                    ship_status[ship.id][shipInfo.GOAL] = dropoffpos
                    #success, cost = NavigateShip(ship, game_map, command_queue)
                    #costthisturn += cost
                    reservedfordropoff += int(constants.DROPOFF_COST * dropoffcostoverhead)
                    dropoffthisturn = True
                    if verbose:
                        logging.info("Ship {} EXPLORING - switching to CONVERTING".format(ship.id))
                    #
                else:
                    ship_status[ship.id][shipInfo.STATE] = shipState.RETURNING
                    ship_status[ship.id][shipInfo.GOAL], ship_status[ship.id][shipInfo.DROPID] = GetClosestStoragePosition(ship.position, me, game_map)
                    #success, cost = NavigateShip(ship, game_map, command_queue)
                    #costthisturn += cost
                    if verbose:
                        logging.info("Ship {} EXPLORING - switching to RETURNING".format(ship.id))
                    #
                #
            elif (ship.halite_amount > return_threshold and returning < numdropoffs+2):
                ship_status[ship.id][shipInfo.STATE] = shipState.RETURNING
                ship_status[ship.id][shipInfo.PAUSE] = False
                ship_status[ship.id][shipInfo.GOAL],ship_status[ship.id][shipInfo.DROPID] = GetClosestStoragePosition(ship.position, me, game_map)
                #success, cost = NavigateShip(ship, game_map, command_queue)
                #costthisturn += cost
                if verbose:
                    logging.info("Ship {} EXPLORING - full enough, switching to RETURNING".format(ship.id))
                #
            elif ship_status[ship.id][shipInfo.GOAL] is not None:
                if ship_status[ship.id][shipInfo.GOAL] == ship.position:
                    ship_status[ship.id][shipInfo.GOAL] = None
                    PauseShip(ship, game_map, command_queue)
                    if verbose:
                        logging.info("Ship {} EXPLORING - reached goal, pausing".format(ship.id))
                    #
                #else:
                #    success, cost = NavigateShip(ship, game_map, command_queue)
                #    costthisturn += cost
                #    if verbose:
                #        logging.info("Ship {} EXPLORING - moving to goal".format(ship.id))
                #    #
                #
            elif game_map[ship.position].halite_amount < min_halite:
                ship_status[ship.id][shipInfo.GOAL] = GetRichestPosition( ship.position, 1, game_map[ship.position].halite_amount==0, False, game_map )
                #success, cost = NavigateShip(ship, game_map, command_queue)
                #costthisturn += cost
                if verbose:
                    logging.info("Ship {} EXPLORING - emptied cell goal, moving to new goal".format(ship.id))
                #
            else:
                PauseShip(ship, game_map, command_queue)
                if verbose:
                    logging.info("Ship {} EXPLORING - pausing".format(ship.id))
                #
            #
        #
    #
    
    for id, info in dropoff_status.items():
        ship_here_id = info[dropInfo.SHIP_HERE]
        ship_near_id = info[dropInfo.SHIP_NEAR]
        logging.info("Dropoff {} Ship Here {} Near {} ".format(id, ship_here_id, ship_near_id))
        if ship_here_id and ship_near_id:
            ship_here = me.get_ship(ship_here_id)
            ship_near = me.get_ship(ship_near_id)
            to_near = game_map.get_unsafe_moves(ship_here.position, ship_near.position)
            UpdateNavPlan(ship_here, game_map, ship_near.position)
            command_queue.append(ship_here.move(to_near[0]))
            to_here = game_map.get_unsafe_moves(ship_near.position, ship_here.position)
            UpdateNavPlan(ship_near, game_map, ship_here.position)
            command_queue.append(ship_near.move(to_here[0]))
            logging.info("Ship Here & Near switched")
        elif ship_here_id:
            ship_here = me.get_ship(ship_here_id)
            success, cost = NavigateShip(ship_here, game_map, command_queue)
            costthisturn += cost
            if not success:
                logging.info("Ship Here Stalled")
                ship_status[ship_here_id][shipInfo.STATE] = shipState.RETURNING
                ship_status[ship_here_id][shipInfo.GOAL] = ship_here.position
                ship_status[ship_here_id][shipInfo.PAUSE] = False
                ship_status[ship_here_id][shipInfo.TURNTAKEN] = True
            #
        elif ship_near_id:
            ship_near = me.get_ship(ship_near_id)
            dropoff_cell = game_map[ship_status[ship_near_id][shipInfo.GOAL]]
            if dropoff_cell.is_occupied and dropoff_cell.ship.owner != me.id:
                # kamikaze dropoff squatter
                logging.info("KAMIKAZE: Ship {} Position {} Goal {} Owner {} Me {}".format(ship_near_id, ship_near.position, ship_status[ship_near_id][shipInfo.GOAL], dropoff_cell.ship.owner, me))
                to_dropoff = game_map.get_unsafe_moves(ship_near.position, ship_status[ship_near_id][shipInfo.GOAL])
                UpdateNavPlan(ship_near, game_map, ship_status[ship_near_id][shipInfo.GOAL])
                command_queue.append(ship_near.move(to_dropoff[0]))
            else:
                # move in carefully
                success, cost = NavigateShip(ship_near, game_map, command_queue)
                costthisturn += cost
                logging.info("Ship Near Moved")
            #
        #
        info[dropInfo.SHIP_HERE] = None
        info[dropInfo.SHIP_NEAR] = None
    #
    
    if verbose:
        logging.info("POST-PASS")
    #
    for ship in me.get_ships():        
        if not ship_status[ship.id][shipInfo.TURNTAKEN]:
            if verbose:
                logging.info("Ship {} moving".format(ship.id))
            #
            success, cost = NavigateShip( ship, game_map, command_queue)
            costthisturn += cost
        #
    #
       
    # If you're on the first turn and have enough halite, spawn a ship.
    # Don't spawn a ship if you currently have a ship at port, though.
    #if game.turn_number <= 1 or (me.halite_amount >= GetShipBuildThreshold(int(numships/2)) and game.turn_number < int(constants.MAX_TURNS*0.6) and not game_map[me.shipyard].is_occupied):
    if game.turn_number <= 1 or \
        ((me.halite_amount >= GetShipBuildThreshold(int(shipfibratio*numships))) and \
        ((game.turn_number - createshipturn) > 2) and \
        game.turn_number < int(constants.MAX_TURNS*0.8) and not \
        homing_begun and not \
        game_map[me.shipyard].is_occupied) and not ship_near_shipyard:
        command_queue.append(game.me.shipyard.spawn())
        createshipturn = game.turn_number
        if verbose:
            logging.info("Create Ship")
        #
    #
    
    # Send your moves back to the game environment, ending this turn.
    game.end_turn(command_queue)
    
    if verbose and game.turn_number == 15:
        break
    #
#